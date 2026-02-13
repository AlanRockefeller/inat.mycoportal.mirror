import json
import requests
from both_api import careful_request

MYCO_BASE_URL = "https://mycoportal.org/portal/api/v2"
USER_AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:96.0) Gecko/20100101 Firefox/96.0"}

def get_occurrence(occid, debug=False):

    url = MYCO_BASE_URL + "/occurrence/" + str(occid) + "?includeMedia=1&includeIdentifications=1"

    try:
        result = careful_request("GET", url, headers=USER_AGENT)
    except SystemExit:
        print("Warning: max retries reached fetching MycoPortal occurrence " + str(occid) + ". Skipping.")
        return None
    except Exception as e:
        print("Warning: exception fetching MycoPortal occurrence " + str(occid) + ": " + repr(e) + ". Skipping.")
        return None

    if result is None:
        print("Warning: got no response for MycoPortal occurrence " + str(occid) + ".")
        return None

    if not isinstance(result, dict):
        print("Warning: unexpected response type for MycoPortal occurrence " + str(occid) + ".")
        return None

    # validate response contains at least one expected field
    expected_fields = ("sciname", "scientificName", "catalogNumber")
    if not any(k in result for k in expected_fields):
        print("Warning: response for occurrence " + str(occid) + " missing expected fields. May not be a valid occurrence.")
        return None

    if debug:
        print("DEBUG get_occurrence raw response:")
        print(json.dumps(result, indent=2))

    return result

def get_media_fallback(occid):

    url = MYCO_BASE_URL + "/occurrence/" + str(occid) + "/media"

    try:
        result = careful_request("GET", url, headers=USER_AGENT)
    except SystemExit:
        print("Warning: max retries reached fetching media for occurrence " + str(occid) + ".")
        return []
    except Exception as e:
        print("Warning: exception fetching media for occurrence " + str(occid) + ": " + repr(e))
        return []

    if result is None:
        return []

    if isinstance(result, list):
        return result
    elif isinstance(result, dict) and "media" in result:
        return result["media"] if isinstance(result["media"], list) else []

    return []

def download_image(url, timeout_seconds=30):

    try:
        response = requests.get(url, headers=USER_AGENT, timeout=timeout_seconds)
    except Exception as e:
        return (None, "exception: " + repr(e))

    if response.status_code != 200:
        return (None, "HTTP status " + str(response.status_code))

    content = response.content
    if len(content) < 5000:
        return (None, "response too small (" + str(len(content)) + " bytes) — likely not an image")

    return (content, None)

def download_images(media_list, preferred_size="medium", max_images=None, debug=False):

    successes = []
    failures = []

    count = 0
    for media in media_list:
        if max_images is not None and count >= max_images:
            break

        # skip non-image formats
        fmt = media.get("format", None)
        if fmt and not str(fmt).startswith("image/"):
            continue

        # pick URL based on size preference
        chosen_url = pick_media_url(media, preferred_size)
        if not chosen_url:
            failures.append((media, None, "no usable URL found in media item"))
            continue

        if debug:
            print("DEBUG downloading image: " + chosen_url)

        image_bytes, error = download_image(chosen_url)

        if image_bytes is not None:
            successes.append((image_bytes, media, chosen_url))
            if debug:
                print("DEBUG download success: " + str(len(image_bytes)) + " bytes")
        else:
            failures.append((media, chosen_url, error))
            if debug:
                print("DEBUG download failed: " + str(error))

        count += 1

    return (successes, failures)

def pick_media_url(media, preferred_size):

    size_keys = {
        "original": ["originalUrl", "mediumUrl", "thumbnailUrl"],
        "medium": ["mediumUrl", "originalUrl", "thumbnailUrl"],
        "thumbnail": ["thumbnailUrl", "mediumUrl", "originalUrl"],
    }

    keys = size_keys.get(preferred_size, size_keys["medium"])

    for key in keys:
        val = media.get(key)
        if val:
            return val

    # last resort: check generic keys
    for key in ("url", "accessUri"):
        val = media.get(key)
        if val:
            return val

    return None
