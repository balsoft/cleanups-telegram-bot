# Author - Narek Tatevosyan public@narek.tel

"""
First, a few callback functions are defined. Then, those functions are passed to
the Dispatcher and registered at their respective places.
Then, the bot is started and runs until we press Ctrl-C on the command line.
Usage:
Example of a bot-user conversation using ConversationHandler.
Send /start to initiate the conversation.
Press Ctrl-C on the command line or send a signal to the process to stop the
bot.
"""

import logging
import os
import boto3
import random
import string
import yaml
import re
import shutil

from notion_client import Client




from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update,KeyboardButton
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler
)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
LANGUAGE,DESCRIPTION,MEDIA,LOCATION  = range(4)


database_id = os.environ['NOTION_DATABASE']

notion = Client(auth=os.environ['NOTION_API_KEY'])
S3_FILE_PREFIX = '/data/dynamic'


session = boto3.session.Session()

BUCKET=os.environ['S3_BUCKET']
PVC_NAME=os.environ['PVC_NAME']
s3_bucket_endpoint = 'https://storage.yandexcloud.net'
s3_client = session.client(
    service_name='s3',
    aws_access_key_id=os.environ['AWS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_KEY'],
    endpoint_url=s3_bucket_endpoint,
)
PHRASES_FILE_PREFIX = '/data/tmpfs'
PHRASES_FILE = 'phrases.yaml'

def read_phrase_in_a_language(phrase,language):
    with open(r'%s/%s' %(PHRASES_FILE_PREFIX,PHRASES_FILE)) as file:
    # The FullLoader parameter handles the conversion from YAML
    # scalar values to Python the dictionary format
        phrase_dict = yaml.load(file, Loader=yaml.FullLoader)
        return phrase_dict[phrase][language]



def start(update: Update, context: CallbackContext) -> int:
    """Starts the conversation and asks to continue"""
    reply_keyboard = [['Հայերեն', 'Русский', 'English']]

    update.message.reply_text(
        
        read_phrase_in_a_language('open_phrase','hy') +'\n' +
        read_phrase_in_a_language('open_phrase','ru') +'\n' +
        read_phrase_in_a_language('open_phrase','en')

        ,
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard,one_time_keyboard=True
        ),
        )
    
    return LANGUAGE


def language(update: Update, context: CallbackContext) -> int:
    print(update.message)
    
    language = update.message.text
    user_name = update.message.chat.first_name
    user_telegram_url = update.message.chat.username
    context.user_data['language'] = ''
    print(context.user_data)
    context.user_data['notion_base_page'] = {

            "parent": {
                "database_id": database_id
            },
            "properties": {
                "Status": {
                    "select": {
                        "name": "Moderation"
                    }
                },

            },
            "children": []
    } 

    if language == 'Հայերեն':
        context.user_data['language'] = 'hy'
    elif language == 'Русский':
        context.user_data['language'] = 'ru'
    elif language == 'English':
        context.user_data['language'] = 'en'
    """Starts the conversation and asks to continue"""
    user_id = str(update.message.chat.id)
    chat_date = str(update.message.date.strftime('%s'))
    report_id = '%s-%s' % (user_id,chat_date)
    context.user_data['notion_base_page']['properties']['id'] = {
                    "title": [
                        {
                            "text": {
                                "content": report_id
                            }
                        },
                    ]
                }
    context.user_data['notion_base_page']['properties']['reported_by'] = {
                    "rich_text": [
                        {
                            "text": {
                                "content": user_name,
                                "link": {
                                    "url": 'https://t.me/%s' % (user_telegram_url)
                                }
                            }
                        }
                    ]	
                }
    print(context.user_data['notion_base_page'])
                
    update.message.reply_text(
        read_phrase_in_a_language('intro',context.user_data['language'])
    )
    update.message.reply_text(
        read_phrase_in_a_language('description',context.user_data['language'])
    )
    return DESCRIPTION

def description(update: Update, context: CallbackContext) -> int:   
    user = update.message.from_user
    context.user_data['done_button'] = read_phrase_in_a_language('done_button',context.user_data['language'])
    report_description = update.message.text
    #print(update.message)
    logger.info("Description of %s: %s", user.first_name,report_description)
    update.message.reply_text(
            read_phrase_in_a_language('media_phrase',context.user_data['language'])
       ## reply_markup = ReplyKeyboardMarkup(
      ##       [KeyboardButton(request_location=True)]
    ##    )
    )
    context.user_data['notion_base_page']['children'].extend([
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{ "type": "text", "text": { "content": "Report description" } }]
                    }
                },
                {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": report_description,
                            }
                        },
                        
                    ]
                }
                },
                {
                 "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{ "type": "text", "text": { "content": "Report Media" } }]
                    }
                },
        ]
            )
    return MEDIA

def media(update: Update, context: CallbackContext) -> int:
    end_message = context.user_data['done_button']
    reply_keyboard = [[end_message]]
    
    user_id = str(update.message.chat.id)
    chat_date = str(update.message.date.strftime('%s'))
    print(user_id,chat_date)
    
    if update.message.text:
        if update.message.text == end_message:
            # when user ends upload process he goes to location 
            update.message.reply_text(
                    read_phrase_in_a_language('location_phrase',context.user_data['language']),
                reply_markup = ReplyKeyboardRemove() )
            return LOCATION
        else:
            update.message.reply_text(
           read_phrase_in_a_language('media_error',context.user_data['language'])
        )   
        return MEDIA
    else:
        update.message.reply_text(
        read_phrase_in_a_language('wait_for_media',context.user_data['language']),
        reply_markup=ReplyKeyboardMarkup(
            reply_keyboard, input_field_placeholder='Done?'
        ),
        )
        if update.message.photo:
            photo_file = update.message.photo[-1].get_file()
            random_suffix =  ''.join(random.choice(string.ascii_lowercase) for i in range(10)) 
            photo_file_name = 'user_photo-%s-%s-%s.jpg' % (random_suffix,chat_date,user_id)
            photo_file.download(S3_FILE_PREFIX+'/'+ photo_file_name)
           # s3_client.upload_file(photo_file_name, BUCKET , photo_file_name)     
           #os.remove(photo_file_name)
            image_url = s3_bucket_endpoint+'/'+BUCKET+'/' + PVC_NAME +'/' + photo_file_name
            context.user_data['notion_base_page']['children'].append({
                "object": "block",
                    "type": "image",
                    "image": {
                        "type": "external",
                        "external": {
                            "url": image_url
                            }
                    }
            })
            update.message.reply_text(
            read_phrase_in_a_language('photo_uploaded',context.user_data['language'])
            )   
            return MEDIA
        if update.message.video:
            vide_file = update.message.video.get_file()
            random_suffix =  ''.join(random.choice(string.ascii_lowercase) for i in range(10)) 
            video_file_name = 'user_video-%s-%s-%s.mp4' % (random_suffix,chat_date,user_id)
            vide_file.download(S3_FILE_PREFIX+'/' + video_file_name)
          #  s3_client.upload_file(video_file_name, BUCKET , video_file_name)     
           # os.remove(video_file_name)
            video_url = s3_bucket_endpoint+'/'+BUCKET+ '/' + PVC_NAME +'/' + video_file_name
            context.user_data['notion_base_page']['children'].append({
                "object": "block",
                    "type": "video",
                    "video": {
                        "type": "external",
                        "external": {
                            "url": video_url
                            }
                    }
            })
            update.message.reply_text(
            read_phrase_in_a_language('video_uploaded',context.user_data['language'])
            )  
            return MEDIA
       




def location(update: Update, context: CallbackContext) -> int:
    """Stores the location and asks for some info about the user."""
    user = update.message.from_user
    gps_regex = r'^([-+]?)([\d]{1,2})(((\.)(\d+)(,)))(\s*)(([-+]?)([\d]{1,3})((\.)(\d+))?)$'
    google_regex = r'https://.*goo.gl/.*'
    yandex_regex = r'https://yandex.*'

    if update.message.location:
        user_location_loc = update.message.location
        context.user_data['notion_base_page']['children'].append(
        {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{ "type": "text", "text": { "content": "Report Location" } }]
                    }
                },
        )
        update.message.reply_text(
        read_phrase_in_a_language('location_done',context.user_data['language'])
        )
        logger.info(
        "Location of %s: %f / %f", user.first_name, user_location_loc.latitude, user_location_loc.longitude )
        google_maps_url = "https://www.google.com/maps/search/?api=1&query=%s,%s" % (user_location_loc.latitude, user_location_loc.longitude)
        context.user_data['notion_base_page']['properties']['Location'] = {
                    "rich_text": [
                        {
                            "text": {
                                "content": "Telegram Location",
                                "link": {
                                    "url": google_maps_url
                                }

                            }
                        }
                    ]			
            
        }
        context.user_data['notion_base_page']['children'].append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                    "type": "text",
                    "text": {
                        "content": "%s-%s" % (user_location_loc.latitude, user_location_loc.longitude),
                        "link": {
                            "url": google_maps_url
                        }
                    }
                    }],
                }
            })
    elif update.message.photo:
        user_location_text = update.message.text
        user_id = str(update.message.chat.id)
        context.user_data['notion_base_page']['children'].append(
        {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{ "type": "text", "text": { "content": "Report Location" } }]
                    }
                },
        )
        chat_date = str(update.message.date.strftime('%s'))
        update.message.reply_text(
        read_phrase_in_a_language('location_done',context.user_data['language'])
        )
        photo_file = update.message.photo[-1].get_file()
        random_suffix =  ''.join(random.choice(string.ascii_lowercase) for i in range(10)) 
        photo_file_name = 'location_photo-%s-%s-%s.jpg' % (random_suffix,chat_date,user_id)
        photo_file.download(S3_FILE_PREFIX+ '/' +photo_file_name)
        #s3_client.upload_file(photo_file_name, BUCKET , photo_file_name)     
       # os.remove(photo_file_name)
        image_url = s3_bucket_endpoint+'/'+BUCKET+'/' + PVC_NAME +'/' + photo_file_name
        context.user_data['notion_base_page']['properties']['Location'] = {
                    "rich_text": [
                        {
                            "text": {
                                "content": "Image in a page",
                            }
                        }
                    ]			
            
        }
        context.user_data['notion_base_page']['children'].append({
            "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {
                        "url": image_url
                        }
                }
        })
    elif update.message.text and (re.match(gps_regex, update.message.text) or re.match(google_regex, update.message.text) or re.match(yandex_regex,update.message.text)):
        context.user_data['notion_base_page']['children'].append(
        {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{ "type": "text", "text": { "content": "Report Location" } }]
                    }
                },
        )
        update.message.reply_text(
        read_phrase_in_a_language('location_done',context.user_data['language'])
        )
        if re.match(gps_regex, update.message.text):
            coordinate_type = "Custom GPS"
            coordinate_url = "https://www.google.com/maps/search/?api=1&query=%s" % (update.message.text)
        elif re.match(google_regex, update.message.text):
            coordinate_type = "Google Maps"
            google_maps_url = re.search(google_regex, update.message.text)
            coordinate_url = google_maps_url.group()
        elif re.match(yandex_regex, update.message.text):
            coordinate_type = "Yandex Maps"
            yandex_maps_url = re.search(yandex_regex, update.message.text)
            coordinate_url = yandex_maps_url.group()
        
        context.user_data['notion_base_page']['properties']['Location'] = {
                    "rich_text": [
                        {
                            "text": {
                                "content": coordinate_type,
                                "link": {
                                    "url":  coordinate_url
                                }
                                
                            }
                        }
                    ]			
            
        }
        context.user_data['notion_base_page']['children'].append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                    "type": "text",
                    "text": {
                        "content": update.message.text,
                        }
                    }]
                }
        })
            
    else:
        update.message.reply_text(read_phrase_in_a_language('location_error',context.user_data['language']))
        return LOCATION
    
    page = notion.pages.create(
    **context.user_data['notion_base_page']
    )
    
    return ConversationHandler.END
    



def cancel(update: Update, context: CallbackContext) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)
    update.message.reply_text(
        read_phrase_in_a_language('cancel_phrase',context.user_data['language']), reply_markup=ReplyKeyboardRemove()
    )
    logger.info(context.user_data)

    return  ConversationHandler.END


def main() -> None:
    """Run the bot."""
    # Create the Updater and pass it your bot's token.
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    shutil.copyfile(r'%s' % (PHRASES_FILE),r'%s/%s' %(PHRASES_FILE_PREFIX,PHRASES_FILE))

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    # Add conversation handler with the states GENDER, PHOTO, LOCATION and BIO
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            LANGUAGE: [ MessageHandler(Filters.text & ~Filters.command, language)],
            DESCRIPTION: [ MessageHandler(Filters.text & ~Filters.command, description)],
            MEDIA: [ MessageHandler(Filters.photo | Filters.video | Filters.text & ~Filters.command, media)],
            LOCATION: [ MessageHandler(Filters.location | Filters.photo | Filters.text & ~Filters.command & ~Filters.command, location)], 
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    dispatcher.add_handler(conv_handler)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()