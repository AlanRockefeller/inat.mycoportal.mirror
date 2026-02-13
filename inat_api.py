import base64
import hashlib
import json
import random
import re
import socket
from string import ascii_uppercase, ascii_lowercase, digits
import time
from datetime import date
from sys import exit
from both_api import build_request_URL, request_pause, careful_request


USER_AGENT = {"User-Agent" : "Mushroom Observer/iNaturalist Mirror"}

APP_ID = "2c5FnA4ASUw8d-3b2vSF5kRFyNcI3smfhiPvyxvFghA"


### PKCE variables ###

VERIFIER_CHARACTERS = list(ascii_uppercase+ascii_lowercase+digits)+["-",".","_","~"]
REDIRECT_URI = "http://127.0.0.1:65432"
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 65432
BROWSER_REQUEST_TIMEOUT = 2

### end PKCE variables ###

if REDIRECT_URI != "http://"+LOCAL_HOST+":"+str(LOCAL_PORT):
    print("iNat application callback URL ("+REDIRECT_URI+") does not match local host/port ("+LOCAL_HOST+", "+str(LOCAL_PORT)+").")
    exit(1)
    
def set_application(application_name, application_id):
    
    global USER_AGENT
    global APP_ID
    
    USER_AGENT = {"User-Agent" : application_name}
    APP_ID = application_id

def build_headers(access_token = None):
    
    if access_token:
        return USER_AGENT | {"Authorization" : access_token}
    else:
        return USER_AGENT
     
def confirm_JWT(jwt, username, timestamp = 0):


    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/users/me"
    params = {}
    
    parsed = careful_request("GET", build_request_URL(base_URL, endpoint, params), headers = build_headers(jwt))
    
    try:
        response_username = parsed["results"][0]["login"]
    except:
        return False
    else:
        return response_username.lower() == username.lower()

def get_param_from_socket(field):


    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:

        s.bind((LOCAL_HOST, LOCAL_PORT))
        s.listen()
        
        conn, addr = s.accept()
            
        if addr[0] != LOCAL_HOST:
            print("\nReceived unexpected connection besides "+LOCAL_HOST+". Quitting.\n")
            exit(1)
        
        with conn:

            print("\nReceived connection from browser. Waiting for you to enter the URL above...")

            timer_start = time.time()
            timed_out = False
            while not timed_out:

                remaining = BROWSER_REQUEST_TIMEOUT - (time.time() - timer_start)
                if remaining <= 0:
                    timed_out = True
                    break

                conn.settimeout(remaining)
                try:
                    data = conn.recv(1024)
                except socket.timeout:
                    timed_out = True
                    break
                
                if not data:
                    timed_out = False
                    break
                    
                request_line = next(filter(lambda x : x.split(" ")[0] == "GET", data.decode("ascii").split("\n")), None)
                
                if request_line:
                
                    splat = request_line.split(" ")[1].split("?")
                    
                    # expect another request for (favicon.ico) without param part from previous attempt. Listen for more
                    if len(splat) < 2:
                        continue
                        
                    endpoint = splat[0]
                    param_part = splat[1]
                    
                    try:
                        param_pairs = [p.split("=") for p in param_part.split("&")]
                    except:
                        print(f"Couldn't interpret parameters in URL in the following line: \"{request_line}\". Quitting.\n")
                        conn.send(("HTTP/1.1 200 OK\n"+"Content-Type: text/html\n\n"+USER_AGENT["User-Agent"]+" couldn't get the authorization code. :(").encode("ascii"))
                        exit(1)
                        
                    else:
                    
                        param_dictionary = dict(param_pairs)
                        
                        if field in param_dictionary:
                            print("Got authorization code (via your browser) from iNat.")                            
                            conn.send(("HTTP/1.1 200 OK\n"+"Content-Type: text/html\n\n"+USER_AGENT["User-Agent"]+" got the authorization code. Thank you!").encode("ascii"))
                            return param_dictionary[field]
                            
                        else:
                            print("Couldn't find code in params. Quitting.\n")
                            conn.send(("HTTP/1.1 200 OK\n"+"Content-Type: text/html\n\n"+USER_AGENT["User-Agent"]+" couldn't get the authorization code. :(").encode("ascii"))
                            exit(1)
                            
                else:
                    print("Couldn't find request URL in data from browser. Quitting.\n")
                    conn.send(("HTTP/1.1 200 OK\n"+"Content-Type: text/html\n\n"+USER_AGENT["User-Agent"]+" couldn't get the authorization code. :(").encode("ascii"))
                    exit(1)
                        
def get_JWT_ROPC(username, password):


    ###### get access token w/ user AND client credentials ######

    request_pause("iNat", 0)

    base_URL = "https://www.inaturalist.org"
    endpoint = "/oauth/token"
    data_payload = {"client_id" : APP_ID,
                            "grant_type" : "password",
                            "username" : username,
                            "password" : password}
                            
    parsed = careful_request("POST", build_request_URL(base_URL, endpoint), data = data_payload, headers = build_headers())
    
    try:
        access_token = parsed["access_token"]
    except:
        print("Did not get access token. Try again.")
        return None
    else:
        print("Got access token.")
            
            
    ###### exchange access token for JWT ######
    
    base_URL = "https://www.inaturalist.org"
    endpoint = "/users/api_token"
    
    parsed = careful_request("GET", build_request_URL(base_URL, endpoint), headers = build_headers() | {"Authorization" : "Bearer "+access_token}) # needs "Bearer " - different from what's done in build_headers
    
    try:
        jwt = parsed["api_token"]
    except:
        print("Didn't get JWT from iNaturalist. Try again.")
        return None
    else:
        return jwt

def get_JWT_PKCE():

    ###### generate verifier/challenge pair ######

    code_verifier = "".join([random.choice(VERIFIER_CHARACTERS) for i in range(128)])
    #code_verifier = "pIUgx4tiqFpaOUz0HMc_QbIyQlL901w8mRmkrmhEJ_E" #corresponding challenge should be "_drLS7o5FwkfUiBhlq2hwJnK_SC6yE7sKOde5O1fdzk"
    #print("CV is "+code_verifier)
    cv_hashed = hashlib.sha256(code_verifier.encode("UTF-8")).digest()
    #print("CV hashed is "+cv_hashed)
    cv_encoded = base64.b64encode(cv_hashed).decode()
    #print("CV base64 encoded is "+cv_encoded)
    cv_detrailed = cv_encoded.split("=")[0]
    #print("CV detrailed is "+cv_detrailed)
    code_challenge = cv_detrailed.replace("+","-").replace("/","_")
    #print("CC is "+code_challenge)
    


    ###### get authorization code from logged-in iNat ######

    base_URL = "https://www.inaturalist.org"
    endpoint = "/oauth/authorize"
    params = {"client_id" : APP_ID, 
                    "redirect_uri" : REDIRECT_URI, 
                    "response_type" : "code", 
                    "code_challenge_method" : "S256",
                    "code_challenge" : code_challenge}
                    
    full_URL = build_request_URL(base_URL, endpoint, params)
    
    request_pause("iNat", 0)
    print("\nMake sure you're not logged in to the wrong iNaturalist account (you probably aren't), and that your browser doesn't have redirects disabled (it probably doesn't). Select the entire following URL and press enter to copy it, then navigate to it in your browser. Log in there if prompted, authorize there and return here.\n")
    print(full_URL)
    
    code = get_param_from_socket("code")
    
    
    
    ###### exchange authorization code for access token ######

    base_URL = "https://www.inaturalist.org"
    endpoint = "/oauth/token"
    params = {"client_id" : APP_ID, 
                    "code" : code, 
                    "redirect_uri" : REDIRECT_URI, 
                    "grant_type" : "authorization_code", 
                    "code_verifier" : code_verifier}
                    
    parsed = careful_request("POST", build_request_URL(base_URL, endpoint, params))
    
    try:
        access_token = parsed["access_token"]
    except:
        print("Did not get access token.")
        return None
    else:
        print("Got access token.")


    ###### exchange access token for JWT ######

    base_URL = "https://www.inaturalist.org"
    endpoint = "/users/api_token"

    parsed = careful_request("GET", build_request_URL(base_URL, endpoint), headers = build_headers() | {"Authorization" : "Bearer "+access_token}) # needs "Bearer " - different from what's done in build_headers

    try:
        JWT = parsed["api_token"]
    except:
        print("Did not get JWT.")
        return None
    else:
        print("Got JWT.")
        return JWT

def get_mirrored_MOIDs(username):


    
    mirroreds = []
    
    first_century = ",".join([str(i) for i in range(1750,2000)])
    following_years = [str(i) for i in range(2000,date.today().year+1)]
    year_strings_to_check = [first_century]+following_years
    
    for year_string in year_strings_to_check:
    
        params = {"user_login" : username, "per_page" : "200", "year" : year_string}
    
        page = 1

        while True:
            
        
            base_URL = "https://api.inaturalist.org/v1"
            endpoint = "/observations"
            
            parsed = careful_request("GET", build_request_URL(base_URL, endpoint, params | {"page" : page}), headers = build_headers())
            
            
            if "results" in parsed:
                results_list = parsed["results"]
                
                # ran out of results (presumably)
                if len(results_list) == 0:
                    break
                
            # ran out of results (presumably)
            else:
                break
        
            for result in results_list:
            
                if "ofvs" in result:
                    for ofv in result["ofvs"]:
                        if ofv["field_id"] == 5005: ## Mushroom Observer URL
                            m_ID = re.match(".*/(\\d{1,6})(\\?|$)", ofv["value"])
                            if m_ID:
                                mirroreds.append(m_ID.group(1))
                                continue
                
            page += 1
        
    return set(mirroreds)
    
def get_mirrored_entries(username):


    params = {"user_login" : username, "per_page" : "200"}
    
    entries = {}
    page = 1

    while True:
        
    
        base_URL = "https://api.inaturalist.org/v1"
        endpoint = "/observations"
        
        parsed = careful_request("GET", build_request_URL(base_URL, endpoint, params | {"page" : page}), headers = build_headers())
        
        
        if "results" in parsed:
            results_list = parsed["results"]
            
            # ran out of results (presumably)
            if len(results_list) == 0:
                break
            
        # ran out of results (presumably)
        else:
            break
    
        for result in results_list:
        
            if "ofvs" in result:
                for ofv in result["ofvs"]:
                    if ofv["field_id"] == 5005: ## Mushroom Observer URL
                        m_ID = re.match(".*/(\\d{1,6})(\\?|$)", ofv["value"])
                        if m_ID:
                            entries[m_ID.group(1)] = str(result["id"])
                            continue
            
        page += 1
        
    return entries

def search_for_name(name):

    parsed = careful_request("GET", build_request_URL("https://api.inaturalist.org", "/v1/taxa", {"q" : name}), headers = build_headers())
    
    search_results = parsed["results"] if "results" in parsed else []
    
    return search_results
 
def create_obs(obj, jwt):


    # try 3 times to post & get obs ID back
    for i in range(3):
    
        base_URL = "https://api.inaturalist.org/v1"
        endpoint = "/observations"
        
        parsed = careful_request("POST", build_request_URL(base_URL, endpoint), data = obj, headers = build_headers(jwt))
            
        try:
            iNatID = str(parsed["id"])
            
        except:
            print("Got result without obs ID. Trying again.")
            continue
            
        else:
            return iNatID
            
    print("Couldn't post obs. Dumping and quitting.\n")
    with open("JSON_dump.json", "w", encoding="utf8") as outf:
        outf.write(json.dumps(parsed, indent = 4, sort_keys = False))
    exit(1)
        
def post_fields(iNatID, fields, jwt):

    for field_ID in fields:
    
        field_value = fields[field_ID]
        
        base_URL = "https://api.inaturalist.org/v1"
        endpoint = "/observation_field_values"
        params = {"observation_id" : int(iNatID), 
                        "observation_field_id" : int(field_ID), 
                        "value" : field_value}
        obj = json.dumps({"observation_field_value" : params})
        
        parsed = careful_request("POST", build_request_URL(base_URL, endpoint), data = obj, headers = build_headers(jwt))

def delete_field(iNatID, field_ID, jwt):

    obs = view_particular(iNatID)
    
    exact_field_ID = None
    
    if "ofvs" in obs:
    
        for ofv in obs["ofvs"]:
            if str(ofv["field_id"]) == field_ID:
                exact_field_ID = str(ofv["id"])
                break
                
    else:
        return
        
    if exact_field_ID == None:
        return
                
    
    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observation_field_values/"+exact_field_ID
    
    parsed = careful_request("DELETE", build_request_URL(base_URL, endpoint), headers = build_headers(jwt))

def post_tag(iNatID, tag, jwt):
    
    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observations/"+iNatID
    
    parsed = careful_request("GET", build_request_URL(base_URL, endpoint), headers = build_headers())

    original_tags = parsed["results"][0]["tags"]
    
    ###
    
    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observations/"+iNatID
    obj = json.dumps({"ignore_photos": "1","observation":{"tag_list":", ".join(original_tags+[tag])}})
    
    parsed = careful_request("PUT", build_request_URL(base_URL, endpoint), data = obj, headers = build_headers(jwt))

def get_existing_proposal(iNatID):

    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observations/"+iNatID
    
    parsed = careful_request("GET", build_request_URL(base_URL, endpoint), headers = build_headers())
    
    try:
        identifications = parsed["results"][0]["identifications"]
        identification = identifications[0]
    
    except:    
        print("Couldn't find expected name proposal. Quitting.\n")
        exit(1)
    
    else:
        if len(identifications) > 1:
            print("Warning: Unexpectedly found multiple name proposals already on iNat observation.")
            
        identification_ID = str(identification["id"])
        taxon_ID = str(identification["taxon_id"])
        iNat_name = str(identification["taxon"]["name"])
        iNat_rank = str(identification["taxon"]["rank"])
        
        return identification_ID, taxon_ID, iNat_name, iNat_rank
    
def update_proposal(identification_ID, obj, jwt):

    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/identifications/"+identification_ID
    
    parsed = careful_request("PUT", build_request_URL(base_URL, endpoint), data = obj, headers = build_headers(jwt))

def post_image(iNatID, image, jwt):

    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observation_photos"
    params = {"observation_photo[observation_id]" : int(iNatID)}
    images = {"file" : image}
                           
    parsed = careful_request("POST", build_request_URL(base_URL, endpoint, params), files = images, headers = build_headers(jwt))

def update_obs(iNatID, obj, jwt):

    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observations/"+iNatID
    
    parsed = careful_request("PUT", build_request_URL(base_URL, endpoint), data = obj, headers = build_headers(jwt))
    
def delete_observation(iNatID, jwt):

    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observations/"+iNatID
    
    parsed = careful_request("DELETE", build_request_URL(base_URL, endpoint), headers = build_headers(jwt))

def name_ID_exists(nameID):
    
    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/taxa/"+nameID
    
    parsed = careful_request("GET", build_request_URL(base_URL, endpoint), headers = build_headers())
    
    try:
        num_results = parsed["total_results"]
    except:
        return False
    else:
        return num_results >= 1

def add_obs_to_project(obs_ID, project_ID, jwt):

    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/project_observations"

    obj = json.dumps({"project_id" : int(project_ID), "observation_id" : int(obs_ID)})
    
    parsed = careful_request("POST", build_request_URL(base_URL, endpoint), data = obj, headers = build_headers(jwt))

def view_particular(iNatID):
    
    base_URL = "https://api.inaturalist.org/v1"
    endpoint = "/observations/"+iNatID
    
    parsed = careful_request("GET", build_request_URL(base_URL, endpoint), headers = build_headers())
    obs = parsed["results"][0]
    
    return obs
    