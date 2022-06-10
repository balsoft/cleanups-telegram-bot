"""Telegram bot for reporting littered locations
Author - Narek Tatevosyan public@narek.tel
"""
# libraries

import logging
import os
import random
import string
import re
import traceback
import ffmpeg


import boto3
import yaml
from notion_client import Client


from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, BotCommand
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

# Required env vars
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
TRANSLATIONS_DB = os.environ["TRANSLATIONS_DB_ID"]
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
DATA_PATH_PREFIX = os.environ["DATA_PATH_PREFIX"]
BUCKET = os.environ["S3_BUCKET"]

# Optional env vars
action_databases = {
    "report_dirty_place": os.environ.get("TRASH_DB_ID"),
    "report_place_for_urn": os.environ.get("URN_DB_ID"),
}

PREFERENCES_DB = os.environ.get("PREFERENCES_DB_ID")
FEEDBACK_DB = os.environ.get("FEEDBACK_DB_ID")
EXCEPTIONS_DB = os.environ.get("EXCEPTIONS_DB_ID")

NOTION_STATIC_PAGE_URL = os.environ.get("NOTION_STATIC_PAGE_URL")

S3_BUCKET_ENDPOINT = os.environ.get(
    "S3_BUCKET_ENDPOINT", "https://storage.yandexcloud.net"
)

LOGLEVEL = os.environ.get("LOGLEVEL", "INFO")


# Enable logging
logging.basicConfig(
    format="%(levelname)s@%(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


notion = Client(auth=NOTION_API_KEY)

action_list = []
for name in action_databases:
    if action_databases[name]:
        action_list.append(name)

assert len(action_list) > 0, "Provide a database for at least one action"

session = boto3.session.Session()

s3_client = session.client(
    service_name="s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    endpoint_url=S3_BUCKET_ENDPOINT,
)
boto3.set_stream_logger("boto3.resources", logging.INFO)
# dir where to store language file , its tmpfs fir in kuberentes
# locally you can set any dir but it should contain /dynamic and /tmpfs subdirs
S3_FILE_PREFIX = f"{DATA_PATH_PREFIX}/dynamic"
PHRASES_FILE_PREFIX = f"{DATA_PATH_PREFIX}/tmpfs"
PHRASES_FILE = "phrases.yaml"

LANGUAGE, ACTION, DESCRIPTION, CONTENT, LOCATION = range(5)

REPORT, FEEDBACK = range(2)

pages = notion.databases.query(**{"database_id": TRANSLATIONS_DB})
phrases = {}
for page in pages["results"]:
    translations = {}
    for prop in page["properties"]:
        if prop != "phrase_name":
            if len(page["properties"][prop]["rich_text"]) > 0:
                translations[prop] = page["properties"][prop]["rich_text"][0][
                    "plain_text"
                ]
            else:
                logger.warning(f"Missing {prop} translation")
    if len(page["properties"]["phrase_name"]["title"]) > 0:
        phrases[
            page["properties"]["phrase_name"]["title"][0]["text"]["content"]
        ] = translations

# logger.debug(yaml.dump(phrases))

language_list = list(phrases["language_name"].keys())
if "LANGUAGES" in os.environ:
    language_list = [
        language.strip() for language in os.environ["LANGUAGES"].split(",")
    ]


def find_phrase_name(phrase: str, possible=None) -> str:
    """Find a phrase name (e.g. done_button) from a phrase (e.g. "Done")
    Assumes that all phrases are unique.
    Optionally, only return one of `possible` phrase names.
    Raises ValueError if no such phrase is in the phrases file.
    """
    for phrase_name in possible or phrases.keys():
        if phrase in phrases[phrase_name].values():
            return phrase_name
    raise ValueError()


def reupload_media(
    media_size, extension: str, user_id: str, chat_date: str, generate_thumbnail=False
) -> str:
    """Download media and re-upload it to S3, returning the URL"""
    random_suffix = "".join(random.choice(string.ascii_lowercase) for i in range(10))
    file_name = f"user_media-{random_suffix}-{chat_date}-{user_id}.{extension}"
    path = f"{S3_FILE_PREFIX}/{file_name}"
    media_size.get_file().download(path)
    s3_client.upload_file(path, BUCKET, file_name)
    if generate_thumbnail:
        thumbnail_file_name = f"{file_name}.thumb.png"
        thumbnail_path = f"{S3_FILE_PREFIX}/{thumbnail_file_name}"
        ffmpeg.input(path, ss=1).filter("scale", 512, -1).output(
            thumbnail_path, vframes=1
        ).run()
        s3_client.upload_file(thumbnail_path, BUCKET, thumbnail_file_name)
    os.remove(path)
    return f"{S3_BUCKET_ENDPOINT}/{BUCKET}/{file_name}"


def find_preferences_page(user: str):
    if PREFERENCES_DB:
        fltr = {
            "database_id": PREFERENCES_DB,
            "filter": {
                "property": "username",
                "title": {"equals": user},
            },
        }
        for page in notion.databases.query(**fltr)["results"]:
            return page


def fetch_preferences_to_userdata(data):
    page = find_preferences_page(data["user_id"])
    if page:
        logger.debug("Fetched user preferences: \n%s", yaml.dump(page))
        lang = page["properties"].get("language")
        if lang and (lang["select"]["name"] in language_list):
            data["language"] = lang["select"]["name"]
        else:
            logger.warning("Language is unknown or unset: %s", lang)


def create_or_update_preferences(data):
    if PREFERENCES_DB:
        page = find_preferences_page(data["user_id"])
        properties = {
            "username": {"title": [{"text": {"content": data["user_id"]}}]},
            "language": {"select": {"name": data["language"]}},
        }

        if page:
            preferences = {"page_id": page["id"], "properties": properties}
            notion.pages.update(**preferences)
        else:
            preferences = {
                "parent": {"database_id": PREFERENCES_DB},
                "properties": properties,
            }
            notion.pages.create(**preferences)


def reset_preferences(user: str):
    if PREFERENCES_DB:
        page = find_preferences_page(user)
        if page:
            notion.blocks.delete(page["id"])


def init(update: Update, context: CallbackContext):
    """Start the conversation"""

    context.user_data["user_full_name"] = update.message.chat.full_name
    context.user_data["user_telegram_username"] = update.message.chat.username
    context.user_data["user_id"] = str(update.message.chat.id)
    context.user_data["chat_date"] = str(update.message.date.strftime("%s"))
    context.user_data["photos"] = []
    context.user_data["videos"] = []
    context.user_data["thumbnail"] = None
    context.user_data["comments"] = []

    logger.info(
        "Starting conversation with %s", context.user_data["user_telegram_username"]
    )

    # Fetch preferences and set context accordingly

    fetch_preferences_to_userdata(context.user_data)

    if len(language_list) == 1:
        context.user_data["language"] = language_list[0]
    elif update.message.from_user.language_code:
        code = update.message.from_user.language_code.lower()
        if code in language_list:
            context.user_data["language"] = code


def report(update: Update, context: CallbackContext) -> int:
    init(update, context)

    context.user_data["flow"] = REPORT
    context.user_data["action"] = None
    context.user_data["description"] = None
    context.user_data["location"] = {}

    # Set action list

    if len(action_list) == 1:
        context.user_data["action"] = action_list[0]

    return request_language(update, context)


def feedback(update: Update, context: CallbackContext) -> int:
    init(update, context)

    context.user_data["flow"] = FEEDBACK

    return request_language(update, context)


def language_by_name(name: str) -> str:
    """Get the language key (e.g. en) corresponding to the language name in the reply (e.g. English)"""

    return list(phrases["language_name"].keys())[
        list(phrases["language_name"].values()).index(name)
    ]


def request_language(update: Update, context: CallbackContext) -> int:
    """Ask for user's language if not already set"""

    if context.user_data.get("language"):
        if context.user_data["flow"] == REPORT:
            return request_action(update, context)
        elif context.user_data["flow"] == FEEDBACK:
            return request_feedback(update, context)
        else:
            raise BaseException("Unexpected flow type %s" % context.user_data["flow"])

    else:
        reply_keyboard = [
            [phrases["language_name"][language.strip()]] for language in language_list
        ]

        reply_text = ""
        for lang in language_list:
            update.message.reply_text(
                phrases["open_phrase"][lang],
                reply_markup=ReplyKeyboardMarkup(
                    reply_keyboard, one_time_keyboard=True
                ),
            )

        return LANGUAGE


def language(update: Update, context: CallbackContext) -> int:
    """Save user's language preference"""

    response = update.message.text

    try:
        context.user_data["language"] = language_by_name(response)
    except ValueError:
        logger.warning("Unknown language %s", response)
        # TODO: Translate this
        update.message.reply_text("Unknown language %s, please try again" % response)
        return request_language(update, context)

    if update.message.chat.username != None:
        create_or_update_preferences(context.user_data)

    lang = context.user_data["language"]

    if context.user_data["flow"] == REPORT:
        return request_action(update, context)
    elif context.user_data["flow"] == FEEDBACK:
        return request_feedback(update, context)
    else:
        raise BaseException("Unexpected flow type %s" % context.user_data["flow"])


def request_action(update: Update, context: CallbackContext) -> int:
    """Request which action the user wants to take if not already set"""
    lang = context.user_data["language"]

    update.message.reply_text(phrases["intro_phrase"][lang])

    if context.user_data.get("action"):
        return request_description(update, lang)
    else:
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
        logger.warning("Unknown action %s", response)
        update.message.reply_text("Unknown action %s, please try again" % response)
        return request_action(update, context)

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

    return request_content(update, lang)


def request_content(update: Update, lang: str) -> int:
    """Ask user to submit media of the location"""

    update.message.reply_text(
        phrases["media_phrase"][lang],
    )

    return CONTENT


def content_markup(lang: str):
    return ReplyKeyboardMarkup(
        [[phrases["done_button"][lang]]], input_field_placeholder="Done?"
    )


def wait_for_content(update: Update, lang: str):
    """Tell user to wait until media is uploaded"""
    update.message.reply_text(
        phrases["wait_for_media"][lang],
    )


def photo_uploaded(update: Update, lang: str) -> int:
    """Notify user that a photo has been uploaded"""
    update.message.reply_text(
        phrases["photo_uploaded"][lang], reply_markup=content_markup(lang), quote=True
    )
    return CONTENT


def video_uploaded(update: Update, lang: str) -> int:
    """Notify user that a video has been uploaded"""
    update.message.reply_text(
        phrases["video_uploaded"][lang], reply_markup=content_markup(lang), quote=True
    )
    return CONTENT


def content_error(update: Update, lang: str) -> int:
    """Notify user that there has been an error during media upload"""
    update.message.reply_text(phrases["media_error"][lang], quote=True)
    return CONTENT


def media_required(update: Update, lang: str) -> int:
    """Tell the user to submit at least one photo or video"""
    update.message.reply_text(phrases["media_required"][lang])
    return CONTENT


def content(update: Update, context: CallbackContext) -> int:
    """Receive & upload media from the user, saving the URL"""

    lang = context.user_data["language"]
    if context.user_data["user_telegram_username"] != None:
        user_telegram_username = context.user_data["user_telegram_username"]
    else:
        user_telegram_username = context.user_data["user_id"]
    chat_date = context.user_data["chat_date"]

    try:
        if update.message.text:
            response = update.message.text

            try:
                find_phrase_name(response, ["done_button"])

                if context.user_data["flow"] == FEEDBACK:
                    return done(update, context)
                elif (
                    len(context.user_data["photos"]) + len(context.user_data["videos"])
                    > 0
                ):
                    return request_location(update, lang)
                else:
                    return media_required(update, lang)
            except ValueError:
                context.user_data["comments"].append(update.message.text)
                return CONTENT

        if update.message.photo:
            wait_for_content(update, lang)

            context.user_data["photos"].append(
                reupload_media(
                    update.message.photo[-1], "jpg", user_telegram_username, chat_date
                )
            )

            return photo_uploaded(update, lang)

        if update.message.video:
            wait_for_content(update, lang)

            generate_thumbnail = (
                len(context.user_data["videos"] + context.user_data["photos"]) == 0
            )

            video = reupload_media(
                update.message.video,
                "mp4",
                user_telegram_username,
                chat_date,
                generate_thumbnail=generate_thumbnail,
            )

            context.user_data["videos"].append(video)

            if generate_thumbnail:
                context.user_data["thumbnail"] = f"{video}.thumb.png"

            return video_uploaded(update, lang)
    except BaseException as exp:
        logger.warning(exp)

    return content_error(update, lang)


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

    gps_regex = r"^(-?\d+\.\d+),\s*(-?\d+\.\d+)$"
    google_regex = r"https://.*goo.gl/.*"
    yandex_regex = r"https://yandex.*"

    lang = context.user_data["language"]
    if context.user_data["user_telegram_username"] != None:

        user_telegram_username = context.user_data["user_telegram_username"]
    else:
        user_telegram_username = context.user_data["user_full_name"]

    chat_date = context.user_data["chat_date"]

    if update.message.location:
        # Stores telegram send location

        context.user_data["location"]["coordinates"] = {
            "lat": update.message.location.latitude,
            "lon": update.message.location.longitude,
        }
    elif update.message.photo:
        # Stores location photo

        try:
            context.user_data["location"]["photo"] = reupload_media(
                update.message.photo[-1], "jpg", user_telegram_username, chat_date
            )
        except BaseException as exp:
            logger.warning(exp)
            return location_error(update, lang)

    elif update.message.text:
        # Stores user provided location via text ( gps regexp or yandex/google maps

        response = update.message.text

        if re.match(google_regex, response):
            context.user_data["location"]["link"] = {
                "text": response,
                "type": "Google Maps",
                "url": re.search(google_regex, response).group(),
            }
        elif re.match(yandex_regex, response):
            context.user_data["location"]["link"] = {
                "text": response,
                "type": "Yandex Maps",
                "url": re.search(yandex_regex, response).group(),
            }
        elif re.match(gps_regex, response):
            search = re.search(gps_regex, response)
            context.user_data["location"]["coordinates"] = {
                "lat": search.group(1),
                "lon": search.group(2),
            }
        else:
            return location_error(update, lang)

    else:
        # Provides error and asks again for the location
        return location_error(update, lang)

    return done(update, context)


def done(update: Update, context: CallbackContext) -> int:
    """Upload the report to notion and end the conversation"""

    lang = context.user_data["language"]
    flow = context.user_data["flow"]

    if flow == REPORT:
        page_id = push_notion_report(context.user_data)
    elif flow == FEEDBACK:
        page_id = push_notion_feedback(context.user_data)
    if context.user_data["user_telegram_username"] != None:
        create_or_update_preferences(context.user_data)

    if NOTION_STATIC_PAGE_URL:
        update.message.reply_text(f"{NOTION_STATIC_PAGE_URL}/{page_id}")

    update.message.reply_text(
        phrases["location_done"][lang]
        if flow == REPORT
        else phrases["thanks_for_feedback"][lang]
    )

    return ConversationHandler.END


def notion_reported_by(data):
    if data.get("user_telegram_username"):
        return {
            "rich_text": [
                {
                    "text": {
                        "content": data["user_full_name"],
                        "link": {
                            "url": "https://t.me/%s" % data["user_telegram_username"]
                        },
                    }
                }
            ]
        }
    else:
        return {
            "rich_text": [
                {
                    "text": {
                        "content": data["user_full_name"],
                    }
                }
            ]
        }


def notion_title(content):
    return {"title": [{"text": {"content": content}}]}


def notion_paragraph(content, code=False):
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "annotations": {"code": code},
                    "text": {"content": content},
                },
            ]
        },
    }


def notion_photo(url):
    return {
        "object": "block",
        "type": "image",
        "image": {"type": "external", "external": {"url": url}},
    }


def notion_video(url):
    return {
        "object": "block",
        "type": "video",
        "video": {"type": "external", "external": {"url": url}},
    }


def notion_link(content, url):
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": content,
                        "link": {"url": url},
                    },
                }
            ],
        },
    }


def notion_heading2(content):
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": content}}]},
    }


def push_notion_report(data):
    """Prepares and submits a notion page with the report"""

    logger.debug(yaml.dump(data))

    reported_by = notion_reported_by(data)

    page_id = notion_title(data["description"])

    report_description_heading = notion_heading2("Report description")

    report_description = notion_paragraph(data["description"])

    report_media_heading = notion_heading2("Report content")

    report_photos = [notion_photo(url) for url in data["photos"]]

    report_videos = [notion_video(url) for url in data["videos"]]

    report_comments = [notion_paragraph(comment) for comment in data["comments"]]

    location_heading = notion_heading2("Report Location")

    photo = data["location"].get("photo")
    coordinates = data["location"].get("coordinates")
    link = data["location"].get("link")

    if coordinates:
        location_url = "https://www.google.com/maps/search/?api=1&query=%s,%s" % (
            coordinates["lat"],
            coordinates["lon"],  #
        )
        location_block = notion_link(
            "%s-%s"
            % (
                coordinates["lat"],
                coordinates["lon"],
            ),
            location_url,
        )
        location_property = {
            "rich_text": [
                {
                    "text": {
                        "content": "Telegram Location",
                        "link": {"url": location_url},
                    }
                }
            ]
        }
    elif photo:
        location_block = notion_photo(photo)
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
        location_block = notion_paragraph(link["text"])
        location_property = {
            "rich_text": [
                {"text": {"content": link["type"], "link": {"url": link["url"]}}}
            ]
        }

    thumbnail = []
    if data["thumbnail"]:
        thumbnail = [notion_photo(data["thumbnail"])]

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
        + thumbnail
        + report_photos
        + report_videos
        + report_comments
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

    logger.debug(yaml.dump(page))

    return notion.pages.create(**page)["id"].replace("-", "")


def request_feedback(update: Update, context: CallbackContext) -> int:
    lang = context.user_data["language"]
    update.message.reply_text(
        phrases["feedback_phrase"][lang],
        reply_markup=content_markup(lang),
    )
    return CONTENT


def push_notion_feedback(data):
    logger.debug(yaml.dump(data))

    reported_by = notion_reported_by(data)

    page_id = notion_title("Feedback by " + data["user_full_name"])

    page = {
        "parent": {"database_id": FEEDBACK_DB},
        "properties": {
            "reported_by": reported_by,
            "id": page_id,
        },
        "children": [notion_paragraph(text) for text in data["comments"]]
        + [notion_photo(url) for url in data["photos"]]
        + [notion_video(url) for url in data["videos"]],
    }

    logger.debug(yaml.dump(page))

    return notion.pages.create(**page)["id"].replace("-", "")


def submit_error_to_notion(update: Update, context: CallbackContext):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    page_id = notion_title(str(context.error))

    page = {
        "parent": {"database_id": EXCEPTIONS_DB},
        "properties": {
            "id": page_id,
            "Exception type": {"select": {"name": context.error.__class__.__name__}},
            "User": {
                "rich_text": [{"text": {"content": update.message.from_user.full_name}}]
            },
        },
        "children": [
            notion_heading2("Exception"),
            notion_paragraph(
                "\n".join(
                    traceback.format_exception(
                        None, context.error, context.error.__traceback__
                    )
                ),
                code=True,
            ),
            notion_heading2("user_data"),
            notion_paragraph(str(context.user_data), code=True),
        ],
    }

    notion.pages.create(**page)


def reset(update: Update, context: CallbackContext) -> int:
    reset_preferences(str(update.message.chat.id))
    context.user_data["language"] = None
    update.message.reply_text(
        "Your preferences have been reset. Press /report to report a location, or /feedback to give feedback."
    )

    return ConversationHandler.END


def cancel(update: Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation."""
    logger.info(
        "User %s canceled the conversation.",
        context.user_data["user_telegram_username"],
    )
    update.message.reply_text(
        phrases["cancel_phrase"][context.user_data.get("language") or "en"],
        reply_markup=ReplyKeyboardRemove(),
    )
    logger.info(context.user_data)

    return ConversationHandler.END


def main() -> None:
    """Run the bot."""
    # Create the Updater and pass it your bot's token.
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

    for lang in language_list:
        updater.bot.set_my_commands(
            [
                BotCommand(command, phrases[f"{command}_command"][lang])
                for command in ["report", "feedback", "cancel", "reset"]
            ]
        )

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # Conversation handler is a state machine
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("report", report),
            CommandHandler("reset", reset),
        ]
        + ([CommandHandler("feedback", feedback)] if FEEDBACK_DB else []),
        states={
            LANGUAGE: [MessageHandler(Filters.text & ~Filters.command, language)],
            ACTION: [MessageHandler(Filters.text & ~Filters.command, action)],
            DESCRIPTION: [MessageHandler(Filters.text & ~Filters.command, description)],
            CONTENT: [
                MessageHandler(
                    Filters.photo | Filters.video | Filters.text & ~Filters.command,
                    content,
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
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("reset", reset)],
    )

    dispatcher.add_handler(conv_handler)

    if EXCEPTIONS_DB:
        dispatcher.add_error_handler(submit_error_to_notion)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == "__main__":
    main()
