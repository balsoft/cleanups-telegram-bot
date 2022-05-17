


from notion_client import Client
import yaml
import boto3
import os
import gmplot
import mimetypes


database_id = os.environ['TRASH_DB_ID']
notion = Client(auth=os.environ['NOTION_API_KEY'])

session = boto3.session.Session()

BUCKET=os.environ['S3_BUCKET']
s3_bucket_endpoint = 'https://storage.yandexcloud.net'
s3_client = session.client(
    service_name='s3',
    aws_access_key_id=os.environ['AWS_KEY_ID'],
    aws_secret_access_key=os.environ['AWS_KEY'],
    endpoint_url=s3_bucket_endpoint,
)


def parse_polygon_from_page(page_id,notion=notion):
    page = notion.pages.retrieve(
    **{ 'page_id': page_id}
    )
    page = page['properties']['polygon']['rich_text'][0]['plain_text']

    polygon_loc_list = [loc.split(", ") for loc in  yaml.load(page,Loader=yaml.FullLoader)['polygon']]
    return [(float(loc[0]),float(loc[1])) for loc in polygon_loc_list]


def parse_marker_from_page(page_id,notion=notion):
    page = notion.pages.retrieve(
    **{'page_id': page_id}
    )
    page = page['properties']['marker']['rich_text'][0]['plain_text']
    marker_list = page.split(", ")
    return (float(marker_list[0]),float(marker_list[1]))

def parse_location_from_yaml(filename):
    with open(filename, 'r') as stream:
        data_loaded = yaml.load(stream,Loader=yaml.FullLoader)
    polygon_loc_list = [loc.split(", ") for loc in  data_loaded['polygon']]
    polygon_map_list = [tuple(loc) for loc in polygon_loc_list]
    marker_list = tuple(data_loaded['marker'].split(','))
    result = {}
    result['polygon']=polygon_map_list
    result ['marker'] =marker_list 
    
    return result



gmap = gmplot.GoogleMapPlotter(40.194554, 44.509529, 13, apikey=os.environ['GMAP_APIKEY'])
clean_colour = 'green'
clean_edge_colour = 'darkgreen'
dirty_colour = 'red'
dirty_edge_colour = 'darkred'



statuses = ['Clean','Dirty']
notion_static_page_url = os.environ['NOTION_STATIC_PAGE_URL']


for status in statuses:
    fltr = {
    "database_id": database_id,
    "filter": {
            "property": "Status",
            "select": {
                "equals": status
            }
        }
    }
    print(status)
    for page in notion.databases.query(**fltr)['results']:
        page_id = page['id'].replace('-','')
        if page['properties']['marker']['rich_text'] != []:
            print(page['properties']['id']['title'][0]['plain_text'])
            marker_loc = parse_marker_from_page(page_id)
            marker_name = page['properties']['id']['title'][0]['plain_text']
            reporter_by = page['properties']['reported_by']['rich_text'][0]['plain_text']
            print(marker_loc)
            if status == 'Dirty':
                label = "!"
                colour = dirty_colour
            else:
                label = "C"
                colour = clean_colour
            gmap.marker(marker_loc[0], marker_loc[1], color=colour, title=marker_name,label=label, info_window="%s <br/> reported by %s <br/> <a href='https://%s/%s' target='_blank'>Details</a>" % (marker_name,reporter_by,notion_static_page_url,page_id) )
        if page['properties']['polygon']['rich_text'] != []:
            polygon = parse_polygon_from_page(page_id)
            print(polygon)
            if status == 'Dirty':
                edge_colour = dirty_edge_colour
                face_colour = dirty_colour
            else:
                edge_colour = clean_edge_colour
                face_colour = clean_colour
            gmap.polygon(*zip(*polygon), face_color=face_colour, edge_color=edge_colour, edge_width=2)

            
map_filename = 'map.html'
gmap.draw(map_filename)
content_type = mimetypes.guess_type(map_filename)[0]

s3_client.upload_file(map_filename, BUCKET ,map_filename,  ExtraArgs={'ContentType': content_type})  