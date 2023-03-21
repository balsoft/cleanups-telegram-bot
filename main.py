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

import firebase_admin
from firebase_admin import credentials, firestore

import uuid

import datetime


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
AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
AWS_SECRET_ACCESS_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
DATA_PATH_PREFIX = os.environ["DATA_PATH_PREFIX"]
BUCKET = os.environ["S3_BUCKET"]
FIREBASE_SERVICE_ACCOUNT_KEY_PATH = os.environ["FIREBASE_SERVICE_ACCOUNT_KEY_PATH"]
FIREBASE_PROJECT_ID = os.environ["FIREBASE_PROJECT_ID"]


S3_BUCKET_ENDPOINT = os.environ.get(
    "S3_BUCKET_ENDPOINT", "https://storage.yandexcloud.net"
)


LOGLEVEL = os.environ.get("LOGLEVEL", "INFO")
TRANSLATIONS_YAML = os.environ.get("TRANSLATIONS_YAML", "translations.yaml")

# Enable logging
logging.basicConfig(
    format="%(levelname)s@%(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY_PATH)
app = firebase_admin.initialize_app(cred, options={"projectId": FIREBASE_PROJECT_ID})
db = firestore.client(app)
collection = db.collection("reports")

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

LANGUAGE, DESCRIPTION, CONTENT, LOCATION = range(4)

REPORT, FEEDBACK = range(2)

phrases = yaml.load(open(TRANSLATIONS_YAML), yaml.SafeLoader)
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
    media_size, extension: str, user_id: str, chat_date: str, is_photo, generate_thumbnail=True
) -> (str, int, int):
    """Download media and re-upload it to S3, returning the URL"""
    random_suffix = "".join(random.choice(string.ascii_lowercase) for i in range(10))
    file_name = f"user_media-{random_suffix}-{chat_date}-{user_id}.{extension}"
    path = f"{S3_FILE_PREFIX}/{file_name}"
    media_size.get_file().download(path)
    s3_client.upload_file(path, BUCKET, file_name)
    if generate_thumbnail:
        thumbnail_file_name = f"{file_name}.thumb.jpg"
        thumbnail_path = f"{S3_FILE_PREFIX}/{thumbnail_file_name}"
        if extension == "jpg" or extension == "png" or extension == "webp":
            ffmpeg.input(path).filter("scale", 512, -1).output(
                thumbnail_path
            ).run()
        else:
            ffmpeg.input(path, ss=1).filter("scale", 512, -1).output(
                thumbnail_path, vframes=1
            ).run()
        s3_client.upload_file(thumbnail_path, BUCKET, thumbnail_file_name)
        os.remove(thumbnail_path)
    os.remove(path)
    return (f"{S3_BUCKET_ENDPOINT}/{BUCKET}/{file_name}", media_size.width, media_size.height)


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
    context.user_data["language"] = update.message.from_user.language_code.lower()

    logger.info(
        "Starting conversation with %s", context.user_data["user_telegram_username"]
    )

    # Fetch preferences and set context accordingly

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

    if context.user_data.get("language") is not None and context.user_data.get("language") in language_list:
        if context.user_data["flow"] == REPORT:
            return request_description(update, context.user_data["language"])
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

    if context.user_data["flow"] == REPORT:
        return request_description(update, context.user_data["language"])
    else:
        raise BaseException("Unexpected flow type %s" % context.user_data["flow"])

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

                if (
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
                    update.message.photo[-1], "jpg", user_telegram_username, chat_date, True
                )
            )

            return photo_uploaded(update, lang)

        if update.message.video:
            wait_for_content(update, lang)

            video = reupload_media(
                update.message.video,
                "mp4",
                user_telegram_username,
                chat_date,
                False
            )

            context.user_data["videos"].append(video)

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
    """Upload the report to firebase and end the conversation"""

    lang = context.user_data["language"]
    flow = context.user_data["flow"]

    if flow == REPORT:
        push_firebase_report(context.user_data)
    update.message.reply_text(
        phrases["location_done"][lang]
    )

    return ConversationHandler.END


def push_firebase_report(data):
    logger.debug(yaml.dump(data))

    id =  str(uuid.uuid1()).upper()

    record = {
        "created_on": int(datetime.datetime.utcnow().timestamp()),
        "status": "moderation",
        "location": data["location"]["coordinates"],
        "user_name": data.get("user_telegram_username") or data["user_full_name"],
        "user_provider_id": "telegram",
        "user_id": "tg://user?id=" + data.get("user_id"),
        "id": id,
        "description": data["description"],
        "photos": [
            {
                "id": str(uuid.uuid1()).upper(),
                "url": url,
                "preview_image_url": f"{url}.thumb.jpg",
                "width": width,
                "height": height,
            }
            for (url, width, height) in data["photos"]
        ],
        "videos": [
            {
                "id": str(uuid.uuid1()).upper(),
                "url": url,
                "preview_image_url": f"{url}.thumb.jpg",
                "width": width,
                "height": height,
            }
            for (url, width, height) in data["videos"]
        ],
    }

    collection.document(id).set(record)


def reset(update: Update, context: CallbackContext) -> int:
    context.user_data["language"] = None
    update.message.reply_text(
        "Your preferences have been reset. Press /report to report a location"
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


def dont_understand(update: Update, context: CallbackContext):
    lang = context.user_data.get(
        "language",
        update.message.from_user.language_code
        if update.message.from_user.language_code in language_list
        else language_list[0],
    )
    update.message.reply_text(phrases["dont_understand"][lang], quote=True)


def main() -> None:
    """Run the bot."""
    # Create the Updater and pass it your bot's token.
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)

    for lang in language_list:
        updater.bot.set_my_commands(
            [
                BotCommand(command, phrases[f"{command}_command"][lang])
                for command in ["report", "feedback", "cancel", "reset"]
            ],
            language_code=lang,
        )

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # Conversation handler is a state machine
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("report", report),
            CommandHandler("start", report),
            CommandHandler("reset", reset),
        ]
        + [MessageHandler(Filters.all, dont_understand)],
        states={
            LANGUAGE: [MessageHandler(Filters.text & ~Filters.command, language)],
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
                    | Filters.text & ~Filters.command & ~Filters.command,
                    location,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("reset", reset),
            MessageHandler(Filters.all, dont_understand),
        ],
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
