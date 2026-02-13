import json
import requests
import time
import urllib.parse
from sys import exit

INAT_API_INTERVAL = 1.5
MO_API_INTERVAL = 5
MYCO_API_INTERVAL = 1.5
FIRST_ATTEMPT_PAUSE = 5
MYCO_FIRST_ATTEMPT_PAUSE = 3
MYCO_MAX_BACKOFF = 30
DEFAULT_TIMEOUT = 30
MAX_ATTEMPTS = 7

sess = requests.Session()
last_iNat_request = 0
last_MO_request = 0
last_MycoPortal_request = 0

def build_request_URL(base_URL, endpoint, params = {}):

    param_string = "" if len(params) == 0 else "?"+"&".join([key+"="+urllib.parse.quote(str(params[key])) for key in params])
    
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
        multi_attempt_pause = FIRST_ATTEMPT_PAUSE*(pow(2, attempt)-1)
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
    
def confirm_json(response):

    if str(response.status_code) != "200":
    
        # print("Did not receive status code 200. Dumping and quitting.\n")
        # with open("json_dump.json", "wb") as outf:
            # outf.write(response.content)
        # exit(1)
    
        return None # receiving 401 from iNat isn't an exitable offense, just means auth failed

    try:
    
        parsed = json.loads(response.content)
        
    except:
    
        if "iNaturalist API is down" in str(response.content[:500]):
            print("iNat API down. Trying again.")
            return None
        
        else:
            print("Got unexpected non-json response. Dumping and quitting.\n")
            with open("non_json_dump.html", "wb") as outf:
                outf.write(response.content)
            exit(1)
            
    else:
            
        return parsed
        
def careful_request(request_type, url, data = None, files = None, headers = None, demand_json = True):

    if url.startswith("https://api.inaturalist.org"):
        platform = "iNat"
    elif url.startswith("https://mushroomobserver.org"):
        platform = "MO"
    elif url.startswith("https://mycoportal.org/portal/api/"):
        platform = "MycoPortal"
    else:
        platform = None
        
    attempt = 0
    while attempt < MAX_ATTEMPTS:
    
        request_pause(platform, attempt)
        attempt += 1
        
        try:
            
            if request_type == "GET":
                response = sess.get(url, data = data, files = files, headers = headers, timeout = DEFAULT_TIMEOUT)
            elif request_type == "POST":
                response = sess.post(url, data = data, files = files, headers = headers, timeout = DEFAULT_TIMEOUT)
            elif request_type == "PUT":
                response = sess.put(url, data = data, files = files, headers = headers, timeout = DEFAULT_TIMEOUT)
            elif request_type == "PATCH":
                response = sess.patch(url, data = data, files = files, headers = headers, timeout = DEFAULT_TIMEOUT)
            elif request_type == "DELETE":
                response = sess.delete(url, data = data, files = files, headers = headers, timeout = DEFAULT_TIMEOUT)
                
            status_code = str(response.status_code)
            
            if status_code != "200":
            
                # unauthorized - don't retry, this is useful information to return (indicated by None)
                if status_code == "401":
                    return None
                    
                elif status_code == "400" and platform == "iNat":
                    print("Got status code 400. This may be a brief hiccup from iNat. Trying again.")
                    # print("Request type: "+request_type)
                    # print("URL: "+url)
                    # print("Data: "+str(data))
                    # print("Headers: "+str(headers))
                    # print("Quitting.\n")
                    
                elif platform:
                    # print("url: "+url)
                    # print("data: "+str(data))
                    print(str(response.content))
                    print("Got status code "+status_code+" from "+platform+". Trying again.")
                else:
                    print("Got status code "+status_code+". Trying again.")
                continue
                
            # reach the content
            if demand_json:
                content = json.loads(response.content)
            else:
                content = response.content
            
        except (requests.exceptions.ConnectionError, ConnectionResetError):
            if platform:
                print("Connection error trying to reach "+platform+". Trying again.")
            else:
                print("Connection error. Trying again.")
            continue
            
        except json.decoder.JSONDecodeError:
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
                
        except requests.exceptions.ReadTimeout:
            if platform:
                print("Read timed out trying to reach "+platform+". Trying again.")
            else:
                print("Read timed out. Trying again.")
            continue
        
        # no exceptions or other problems. return the content
        else:
            return content
    
    print("Maximum request attempts reached. Quitting.\n")
    exit(0)