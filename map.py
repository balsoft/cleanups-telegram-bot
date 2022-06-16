from notion_client import Client
import yaml
import boto3
import os
import gmplot
import mimetypes
import json
import datetime

database_id = os.environ["TRASH_DB_ID"]
notion = Client(auth=os.environ["NOTION_API_KEY"])


if "MAP_CENTER" in os.environ:
    map_center = [float(t) for t in os.environ["MAP_CENTER"].split(",")]
else:
    map_center = [40.194554, 44.509529]

if "MAP_SIZE" in os.environ:
    map_size = os.environ["MAP_SIZE"]
else:
    map_size = 13

if "MAP_NAME" in os.environ:
    map_name = os.environ["MAP_NAME"]
else:
    map_name = "map.html"

session = boto3.session.Session()

BUCKET = os.environ["S3_BUCKET"]
S3_BUCKET_ENDPOINT = os.environ.get(
    "S3_BUCKET_ENDPOINT", "https://storage.yandexcloud.net"
)
s3_client = session.client(
    service_name="s3",
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    endpoint_url=S3_BUCKET_ENDPOINT,
)


def parse_polygon_from_page(page_id, notion=notion):
    """Function to create polygon from polygon property inside a page"""
    page = notion.pages.retrieve(**{"page_id": page_id})
    page = page["properties"]["polygon"]["rich_text"][0]["plain_text"]

    polygon_loc_list = [
        loc.split(", ") for loc in yaml.load(page, Loader=yaml.FullLoader)["polygon"]
    ]
    return [(float(loc[0]), float(loc[1])) for loc in polygon_loc_list]


def parse_marker_from_page(page_id, notion=notion):
    """Function to create marker from polygon property inside a page"""

    page = notion.pages.retrieve(**{"page_id": page_id})
    page = page["properties"]["marker"]["rich_text"][0]["plain_text"]
    marker_list = page.split(", ")
    return (float(marker_list[0]), float(marker_list[1]))


def parse_location_from_yaml(filename):
    """Just a function for local map debug"""

    with open(filename, "r") as stream:
        data_loaded = yaml.load(stream, Loader=yaml.FullLoader)
    polygon_loc_list = [loc.split(", ") for loc in data_loaded["polygon"]]
    polygon_map_list = [tuple(loc) for loc in polygon_loc_list]
    marker_list = tuple(data_loaded["marker"].split(","))
    result = {}
    result["polygon"] = polygon_map_list
    result["marker"] = marker_list

    return result


gmap = gmplot.GoogleMapPlotter(
    map_center[0], map_center[1], map_size, apikey=os.environ["GMAP_APIKEY"]
)
clean_colour = "green"
clean_edge_colour = "darkgreen"
dirty_colour = "red"
dirty_edge_colour = "darkred"
pending_colour = "blue"


statuses = ["Clean", "Dirty", "Pending Clean-up"]
notion_static_page_url = os.environ["NOTION_STATIC_PAGE_URL"]

"""We parse only pages with statuses that we provide"""

for status in statuses:
    fltr = {
        "database_id": database_id,
        "filter": {"property": "Status", "select": {"equals": status}},
    }
    print(status)
    for page in notion.databases.query(**fltr)["results"]:
        page_id = page["id"].replace("-", "")
        if page["properties"]["marker"]["rich_text"] != []:
            """If page contains marker we parse it"""

            print(page["properties"]["id"]["title"][0]["plain_text"])
            marker_loc = parse_marker_from_page(page_id)
            description = page["properties"]["id"]["title"][0]["plain_text"]
            marker_name = description.split("\n")[0]
            reporter_by = page["properties"]["reported_by"]["rich_text"][0][
                "plain_text"
            ]
            print(marker_loc)
            if status == "Dirty":
                label = "!"
                colour = dirty_colour
            elif status == "Clean":
                label = "C"
                colour = clean_colour
            elif status == "Pending Clean-up":
                label = "P"
                colour = pending_colour
            else:
                continue

            image_html = ""
            if page.get("cover"):
                image_html = (
                    "<img src=%s alt='Image of location' style='position: relative; padding: 0px; margin-right: 10px; max-height: 200px; width=100%%;' /> <br/>"
                    % page["cover"]["external"]["url"]
                )

            if (
                status == "Pending Clean-up"
                and page["properties"].get("Date")
                and page["properties"].get("Announcement (Telegram)")
                and page["properties"].get("Announcement (Facebook)")
            ):
                description_html = (
                    "<h3>%s</h3><b>We're going to clean up here on %s!</b><br/>More details here: <a href=%s target=_blank>Telegram</a> <a href=%s target=_blank>Facebook</a><br/>"
                    % (
                        description,
                        datetime.datetime.strptime(
                            page["properties"]["Date"]["date"]["start"],
                            "%Y-%m-%d",
                        ).strftime("%b %d, %Y"),
                        page["properties"]["Announcement (Telegram)"]["url"],
                        page["properties"]["Announcement (Facebook)"]["url"],
                    )
                )
            else:
                description_html = f"<h3>{description}</h3>"

            reported_by_html = f"Reported by {reporter_by}<br/>"
            details_html = f"<a href='https://{notion_static_page_url}/{page_id} target='_blank'>Report details</a>"

            gmap.marker(
                marker_loc[0],
                marker_loc[1],
                color=colour,
                title=marker_name,
                label=label,
                info_window=description_html
                + image_html
                + reported_by_html
                + details_html,
            )
        if page["properties"]["polygon"]["rich_text"] != []:
            """If page contains polygon we parse it"""
            polygon = parse_polygon_from_page(page_id)
            print(polygon)
            if status == "Dirty":
                edge_colour = dirty_edge_colour
                face_colour = dirty_colour
            else:
                edge_colour = clean_edge_colour
                face_colour = clean_colour
            gmap.polygon(
                *zip(*polygon),
                face_color=face_colour,
                edge_color=edge_colour,
                edge_width=2,
            )


map_filename = map_name
gmap.draw(map_filename)
content_type = mimetypes.guess_type(map_filename)[0]
"""When map is ready it is uploaded to the cloud"""
s3_client.upload_file(
    map_filename, BUCKET, map_filename, ExtraArgs={"ContentType": content_type}
)
