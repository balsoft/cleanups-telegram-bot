# Author - Narek Tatevosyan public@narek.tel

# libraries

import logging
import os
import boto3
import random
import string
import yaml
import re
import shutil

from notion_client import Client


from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, KeyboardButton
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

logger = logging.getLogger(__name__)


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]


notion = Client(auth=os.environ["NOTION_API_KEY"])
# this is language and feature flags that can be enabled via env vars
if "LANGUAGES" in os.environ:
    language_list = [
        language.strip() for language in os.environ["LANGUAGES"].split(",")
    ]
else:
    language_list = ["hy", "ru", "en"]

if "ACTIONS" in os.environ:
    action_list = [action.strip() for action in os.environ["ACTIONS"].split(",")]
else:
    action_list = ["report_dirty_place", "report_place_for_urn"]

action_databases = {
    "report_dirty_place": os.environ.get("TRASH_DB_ID"),
    "report_place_for_urn": os.environ.get("URN_DB_ID"),
}

session = boto3.session.Session()

BUCKET = os.environ["S3_BUCKET"]
s3_bucket_endpoint = "https://storage.yandexcloud.net"
s3_client = session.client(
    service_name="s3",
    aws_access_key_id=os.environ["AWS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_KEY"],
    endpoint_url=s3_bucket_endpoint,
)
boto3.set_stream_logger("boto3.resources", logging.INFO)
DATA_PATH_PREFIX = os.environ[
    "DATA_PATH_PREFIX"
]  # dir where to store language file , its tmpfs fir in kuberentes - locally you can set any dir but it should contain /dynamic and /tmpfs subdirs
S3_FILE_PREFIX = "%s/dynamic" % (DATA_PATH_PREFIX)
PHRASES_FILE_PREFIX = "%s/tmpfs" % (DATA_PATH_PREFIX)
PHRASES_FILE = "phrases.yaml"

LANGUAGE, ACTION, DESCRIPTION, MEDIA, LOCATION = range(5)

phrases = yaml.load(open(PHRASES_FILE), Loader=yaml.FullLoader)


def find_phrase_name(phrase: str, possible=None, languages=None) -> str:
    """Find a phrase name (e.g. done_button) from a phrase (e.g. "Done")
    Assumes that all phrases are unique.
    Optionally, only return one of `possible` phrase names, or search for phrases only in one of `languages`.
    Raises ValueError if no such phrase is in the phrases file.
    """
    for phrase_name in possible or phrases.keys():
        if phrase in languages or phrases[phrase_name].values():
            return phrase_name
    raise ValueError()


def reupload_media(media, extension: str, user_id: str, chat_date: str) -> str:
    """Download media and re-upload it to S3, returning the URL"""
    random_suffix = "".join(random.choice(string.ascii_lowercase) for i in range(10))
    file_name = "user_media-%s-%s-%s.%s" % (
        random_suffix,
        chat_date,
        user_id,
        extension,
    )
    path = "%s/%s" % (S3_FILE_PREFIX, file_name)
    media.download(path)
    s3_client.upload_file(path, BUCKET, file_name)
    os.remove(path)
    return "%s/%s/%s" % (s3_bucket_endpoint, BUCKET, file_name)


def start(update: Update, context: CallbackContext) -> int:
    """Start the conversation"""
    context.user_data["user_first_name"] = update.message.chat.first_name
    context.user_data["user_telegram_username"] = update.message.chat.username
    context.user_data["chat_date"] = str(update.message.date.strftime("%s"))

    logger.info(
        "Starting conversation with %s", context.user_data["user_telegram_username"]
    )

    return request_language(update, context)


def request_language(update: Update) -> int:
    """Ask for user's language"""
    reply_keyboard = [
        [phrases["language_name"][language.strip()] for language in language_list]
    ]

    reply_text = ""
    for language in language_list:
        reply_text += phrases["open_phrase"][language] + "\n"
    update.message.reply_text(
        reply_text,
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )

    return LANGUAGE


def language(update: Update, context: CallbackContext) -> int:
    """Save user's language preference"""
    # TODO: remember the language outside conversations

    response = update.message.text

    try:
        """Get the language key (e.g. en)
        corresponding to the language name in the reply (e.g. English)"""
        context.user_data["language"] = phrases["language_name"].keys()[
            phrases["language_name"].values().index(response)
        ]
    except ValueError:
        logger.warn("Unknown language %s", response)
        # TODO: Translate this
        update.message.reply_text("Unknown language %s, please try again" % response)
        return request_language(update)

    lang = context.user_data["language"]

    update.message.reply_text(phrases["intro_phrase"][lang])

    if len(action_list) > 1:
        return request_action(update, lang)
    else:
        return request_description(update, lang)


def request_action(update: Update, lang: str) -> int:
    """Request which action the user wants to take"""

    reply_keyboard = [[phrases[action][lang] for action in action_list]]

    update.message.reply_text(
        phrases["action_phrase"][lang],
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )

    return ACTION


def action(update: Update, context: CallbackContext) -> int:
    """Remember which action the user wants to take"""

    response = update.message.text
    lang = context.user_data["language"]

    try:
        context.user_data["action"] = find_phrase_name(response, action_list)
    except ValueError:
        logger.warn("Unknown action %s", response)
        update.message.reply_text("Unknown action %s, please try again" % response)
        return request_action()

    return request_description(update, lang)


def request_description(update: Update, lang: str) -> int:
    """Request a description of the location"""

    update.message.reply_text(
        phrases["description_phrase"][lang], reply_markup=ReplyKeyboardRemove()
    )

    return DESCRIPTION


def description(update: Update, context: CallbackContext) -> int:
    """Sets the location decription"""

    response = update.message.text
    lang = context.user_data["language"]

    context.user_data["description"] = response

    return request_media(update, lang)


def request_media(update: Update, lang: str) -> int:
    """Ask user to submit media of the location"""

    update.message.reply_text(
        phrases["media_phrase"][lang],
    )

    return MEDIA


def wait_for_media(update: Update, lang: str):
    """Tell user to wait until media is uploaded"""
    reply_keyboard = [[phrases["done_button"][lang]]]
    update.message.reply_text(
        phrases["wait_for_media"][lang],
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, input_field_placeholder="Done?"
        ),
    )


def photo_uploaded(update: Update, lang: str) -> int:
    """Notify user that a photo has been uploaded"""
    update.message.reply_text(phrases["photo_uploaded"][lang], quote=True)
    return MEDIA


def video_uploaded(update: Update, lang: str) -> int:
    """Notify user that a video has been uploaded"""
    update.message.reply_text(phrases["video_uploaded"][lang], quote=True)
    return MEDIA


def media_error(update: Update, lang: str) -> int:
    """Notify user that there has been an error during media upload"""
    update.message.reply_text(phrases["media_error"][lang], quote=True)
    return MEDIA


def media(update: Update, context: CallbackContext) -> int:
    """Receive & upload media from the user, saving the URL"""

    lang = context.user_data["language"]
    user_telegram_username = context.user_data["user_telegram_username"]
    chat_date = context.user_data["chat_date"]

    context.user_data["photos"] = []
    context.user_data["videos"] = []

    if update.message.text:
        response = update.message.text

        try:
            find_phrase_name(response, ["done_button"])
        except ValueError:
            logger.warn("Unknown response: %s", response)
            return media_error(update, lang)

        return request_location(update, lang)
    elif update.message.photo:
        wait_for_media(update, lang)

        context.user_data["photos"].append(
            reupload_media(
                update.message.photo, "jpg", user_telegram_username, chat_date
            )
        )

        return photo_uploaded(update, lang)
    elif update.message.video:
        wait_for_media(update, lang)

        context.user_data["videos"].append(
            reupload_media(
                update.message.video, "mp4", user_telegram_username, chat_date
            )
        )

        return video_uploaded(update, lang)
    else:
        return media_error(update, lang)


def request_location(update: Update, lang: str) -> int:
    """Request the precise location of the place in question"""
    update.message.reply_text(
        phrases["location_phrase"][lang],
        reply_markup=ReplyKeyboardRemove(),
    )
    return LOCATION


def location_error(update: Update, lang: str) -> int:
    """Tell the user there has been an error parsing the location"""
    update.message.reply_text(phrases["location_error"][lang])
    return LOCATION


def location(update: Update, context: CallbackContext) -> int:
    """Save the location which the user has sent"""

    gps_regex = (
        r"^([-+]?)([\d]{1,2})(((\.)(\d+)(,)))(\s*)(([-+]?)([\d]{1,3})((\.)(\d+))?)$"
    )
    google_regex = r"https://.*goo.gl/.*"
    yandex_regex = r"https://yandex.*"

    lang = context.user_data["language"]
    user_telegram_username = context.user_data["user_telegram_username"]
    chat_date = context.user_data["chat_date"]

    context.user_data["location"] = {}

    if update.message.location:
        """Stores telegram send location"""

        context.user_data["location"]["coordinates"][
            "lat"
        ] = update.message.location.latitude
        context.user_data["location"]["coordinates"][
            "lon"
        ] = update.message.location.longitude
    elif update.message.photo:
        """Stores location photo"""

        context.user_data["location"]["photo"] = reupload_media(
            update.message.photo, "jpg", user_telegram_username, chat_date
        )

    elif update.message.text:
        """Stores user provided location via text ( gps regexp or yandex/google maps"""

        response = update.message.text

        if re.match(google_regex, response):
            context.user_data["location"]["link"]["type"] = "Google Maps"
            context.user_data["location"]["link"]["url"] = re.search(
                google_regex, response
            ).group()
        elif re.match(yandex_regex, response):
            context.user_data["location"]["link"]["type"] = "Yandex Maps"
            context.user_data["location"]["link"]["url"] = re.search(
                yandex_regex, response
            ).group()
        else:
            return location_error(update, lang)

        context.user_data["location"]["link"]["text"] = response

    else:
        """Provides error and asks again for the location"""
        return location_error(update, lang)

    return done(update, context)


def done(update: Update, context: CallbackContext) -> int:
    """Upload the report to notion and end the conversation"""
    push_notion(context.user_data)

    update.message.reply_text(phrases["location_done"][context.user_data["language"]])

    return ConversationHandler.END


def push_notion(data):
    """Prepares and submits a notion page with the report"""

    lang = data["language"]

    reported_by = {
        "rich_text": [
            {
                "text": {
                    "content": data["user_first_name"],
                    "link": {
                        "url": "https://t.me/%s" % (data["user_telegram_username"])
                    },
                }
            }
        ]
    }

    page_id = {"title": [{"text": {"content": data["description"]}}]}

    report_description_heading = {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": "Report description"}}]
        },
    }
    report_description = (
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": report_description,
                        },
                    },
                ]
            },
        },
    )

    report_media_heading = {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": "Report Media"}}]
        },
    }

    report_photos = [
        {
            "object": "block",
            "type": "image",
            "image": {"type": "external", "external": {"url": url}},
        }
        for url in data["photos"]
    ]

    report_videos = [
        {
            "object": "block",
            "type": "video",
            "image": {"type": "external", "external": {"url": url}},
        }
        for url in data["videos"]
    ]

    location_heading = {
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": "Report Location"}}]
        },
    }

    photo = data["location"]["photo"]
    coordinates = data["location"]["coordinates"]
    link = data["location"]["link"]

    if coordinates:
        location_url = "https://www.google.com/maps/search/?api=1&query=%s,%s" % (
            coordinates["lat"],
            coordinates["lon"],
        )
        location_block = {
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "%s-%s"
                                % (
                                    coordinates["lat"],
                                    coordinates["lon"],
                                ),
                                "link": {"url": location_url},
                            },
                        }
                    ],
                },
            }
        }
        location_property = {
            "rich_text": [
                {
                    "text": {
                        "content": "Telegram Location",
                        "link": {"url": google_maps_url},
                    }
                }
            ]
        }
    elif photo:
        location_block = {
            "object": "block",
            "type": "image",
            "image": {"type": "external", "external": {"url": photo}},
        }
        location_property = {
            "rich_text": [
                {
                    "text": {
                        "content": "Image in a page",
                    }
                }
            ]
        }
    elif link:
        location_block = {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": link["text"],
                        },
                    }
                ]
            },
        }
        location_property = {
            "rich_text": [
                {"text": {"content": link["type"], "link": {"url": link["url"]}}}
            ]
        }

    page = {
        "parent": {"database_id": action_databases[data["action"]]},
        "properties": {
            "Status": {"select": {"name": "Moderation"}},
            "reported_by": reported_by,
            "id": page_id,
            "Location": location_property,
        },
        "children": [
            report_description_heading,
            report_description,
            report_media_heading,
        ]
        + report_photos
        + report_videos
        + [location_heading, location_block],
    }

    if coordinates:
        page["properties"]["marker"] = {
            "rich_text": [
                {
                    "text": {
                        "content": "%s, %s" % (coordinates["lat"], coordinates["lon"])
                    }
                }
            ]
        }

    notion.pages.create(**page)


def cancel(update: Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation."""
    logger.info(
        "User %s canceled the conversation.",
        context.user_data["user_telegram_username"],
    )
    update.message.reply_text(
        phrases["cancel_phrase"][context.user_data["language"]],
        reply_markup=ReplyKeyboardRemove(),
    )
    logger.info(context.user_data)

    return ConversationHandler.END


def main() -> None:
    """Run the bot."""
    # Create the Updater and pass it your bot's token.
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # Conversation handler is a state machine
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LANGUAGE: [MessageHandler(Filters.text & ~Filters.command, language)],
            ACTION: [MessageHandler(Filters.text & ~Filters.command, action)],
            DESCRIPTION: [MessageHandler(Filters.text & ~Filters.command, description)],
            MEDIA: [
                MessageHandler(
                    Filters.photo | Filters.video | Filters.text & ~Filters.command,
                    media,
                )
            ],
            LOCATION: [
                MessageHandler(
                    Filters.location
                    | Filters.photo
                    | Filters.text & ~Filters.command & ~Filters.command,
                    location,
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    dispatcher.add_handler(conv_handler)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == "__main__":
    main()
