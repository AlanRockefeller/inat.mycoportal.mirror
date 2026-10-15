import json
import requests
import time
import urllib.parse
from sys import exit

class MaxRequestAttemptsExceeded(Exception):
    pass

class RequestRejected(MaxRequestAttemptsExceeded):
    """The server explicitly rejected a request without applying it."""

    def __init__(self, status_code, content=None):
        self.status_code = status_code
        self.content = content
        super().__init__("Request rejected (HTTP " + str(status_code) + ").")

class AmbiguousRequestError(MaxRequestAttemptsExceeded):
    """A write may have succeeded; repeating it could create duplicates."""

INAT_API_INTERVAL = 1.5
MO_API_INTERVAL = 5
MYCO_API_INTERVAL = 1.5
FIRST_ATTEMPT_PAUSE = 5
MAX_BACKOFF = 320
MYCO_FIRST_ATTEMPT_PAUSE = 3
MYCO_MAX_BACKOFF = 30
DEFAULT_TIMEOUT = 30
MAX_ATTEMPTS = 7

sess = requests.Session()
last_iNat_request = 0
last_MO_request = 0
last_MycoPortal_request = 0

def build_request_URL(base_URL, endpoint, params = None):

    param_string = "?" + urllib.parse.urlencode(params) if params else ""
    
    request_URL = base_URL+endpoint+param_string
    
    return request_URL
    
def request_pause(platform, attempt = 0):

    global last_iNat_request
    global last_MO_request
    global last_MycoPortal_request
    
    current_time = time.time()
    
    if platform == "iNat":
        API_pause = max(INAT_API_INTERVAL - (current_time-last_iNat_request), 0)
    elif platform == "MO":
        API_pause = max(MO_API_INTERVAL - (current_time-last_MO_request), 0)
    elif platform == "MycoPortal":
        API_pause = max(MYCO_API_INTERVAL - (current_time-last_MycoPortal_request), 0)
    elif platform == None:
        API_pause = 2
    else:
        print("Unknown platform "+str(platform)+". Quitting.\n")
        exit(1)
        
    if platform == "MycoPortal":
        multi_attempt_pause = min(MYCO_FIRST_ATTEMPT_PAUSE*(pow(2, attempt)-1), MYCO_MAX_BACKOFF)
    else:
        multi_attempt_pause = min(FIRST_ATTEMPT_PAUSE*(pow(2, attempt)-1), MAX_BACKOFF)
    final_pause = max(API_pause, multi_attempt_pause)
    
    if final_pause > 0:
        #print("Pausing "+str(round(final_pause,2))+" seconds.")
        time.sleep(final_pause)
    
    if platform == "iNat":
        last_iNat_request = time.time()
    elif platform == "MO":
        last_MO_request = time.time()
    elif platform == "MycoPortal":
        last_MycoPortal_request = time.time()
    elif platform == None:
        pass
    else:
        print("Unknown platform "+str(platform)+". Quitting.\n")
        exit(1)
    
def careful_request(request_type, url, data = None, files = None, headers = None,
                    demand_json = True, retry_safe = None, timeout = DEFAULT_TIMEOUT):
    """Retry reads and idempotent writes; never blindly repeat a POST."""

    request_type = request_type.upper()
    if request_type not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise ValueError("Unsupported request type: " + request_type)
    if retry_safe is None:
        retry_safe = request_type != "POST"

    hostname = urllib.parse.urlparse(url).hostname
    if hostname in ("api.inaturalist.org", "www.inaturalist.org", "inaturalist.org"):
        platform = "iNat"
    elif hostname in ("mushroomobserver.org", "www.mushroomobserver.org"):
        platform = "MO"
    elif hostname in ("mycoportal.org", "www.mycoportal.org"):
        platform = "MycoPortal"
    else:
        platform = None

    if platform == "iNat" and isinstance(data, str) and files is None:
        headers = {"Content-Type": "application/json"} | (headers or {})
        
    attempt = 0
    while attempt < MAX_ATTEMPTS:
    
        request_pause(platform, attempt)
        attempt += 1
        
        try:
            
            response = getattr(sess, request_type.lower())(
                url, data=data, files=files, headers=headers, timeout=timeout)
            status_code = response.status_code

            if not 200 <= status_code < 300:
                # Authentication probes use None; writes must report failure.
                if status_code == 401 and request_type == "GET":
                    return None
                if 400 <= status_code < 500 and status_code not in (408, 429):
                    raise RequestRejected(status_code, response.content.decode("utf8", errors="replace"))
                if not retry_safe and status_code != 429:
                    raise AmbiguousRequestError("HTTP " + str(status_code) +
                                                ": write outcome is unknown; not retrying.")
                print("Got status code " + str(status_code) + ". Trying again.")
                continue
                
            # reach the content
            if demand_json and response.content:
                content = json.loads(response.content)
            elif demand_json:
                content = {}
            else:
                content = response.content
            
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionResetError) as e:
            if not retry_safe:
                raise AmbiguousRequestError("Write outcome is unknown; not retrying.") from e
            if platform:
                print("Connection error trying to reach "+platform+". Trying again.")
            else:
                print("Connection error. Trying again.")
            continue
            
        except json.decoder.JSONDecodeError:
            if not retry_safe:
                raise AmbiguousRequestError("Write returned invalid JSON; not retrying.")
            if "iNaturalist API is down" in str(response.content)[:500]:
                print("iNaturalist API is down. Trying again.")
                continue
            else:
                if platform:
                    print("Unexpected non-JSON response from "+platform+". Dumping and trying again.")
                else:
                    print("Unexpected non-JSON response. Dumping and trying again.")
                with open("non_JSON_dump.html", "wb") as outf:
                    outf.write(response.content)
                continue
                
        # no exceptions or other problems. return the content
        else:
            return content
    
    raise MaxRequestAttemptsExceeded("Maximum request attempts reached. Quitting.")
