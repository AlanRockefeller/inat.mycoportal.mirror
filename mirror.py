import argparse
import csv
import json
import os
import math
import re
import time
from datetime import date, datetime
from sys import exit
from threading import Timer
from urllib.parse import urlparse, parse_qs
import inat_api
import mo_api
import myco_api


TAXON_DICTIONARY_FILENAME = "dictionary.txt"
LOG_FILENAME = "log.txt"
SETTINGS_FILENAME = "PRIVATE settings.txt"
COMPLETES_FILENAME = "completes.txt"
INCOMPLETES_FILENAME = "incompletes.txt"
BACKUP_FOLDER = "backup"
MONTHS = {"01":"Jan.", "02":"Feb.", "03":"Mar.", "04":"Apr.", "05":"May", "06":"Jun.", "07":"Jul.", "08":"Aug.", "09":"Sep.", "10":"Oct.", "11":"Nov.", "12":"Dec."}
CONFIDENCES = {3:"I'd Call It That", 2:"Promising", 1:"Could Be", 0:"No Opinion", -1:"Doubtful", -2:"Not Likely", -3:"As If!"}
INAT_UPDATE_INTERVAL = 300
INAT_JWT_LIFESPAN = 24*60*60
MAX_EXPECTED_MIRROR_TIME = 600
QUIT_PAUSE_TIME = 5
OBSES_PER_MO_PAGE = 100

#mapping iNat ranks to their usage/abbreviation in MO names
BINOMIAL_RANKS = {"subgenus" : "subg.", "section" : "sect.", "subsection" : "subsect.", "series" : "series"}
TRINOMIAL_RANKS = {"subspecies" : "subsp.", "variety" : "var.", "form" : "f."}

MYCO_COMPLETES_FILENAME = "myco_completes.txt"
MYCO_INCOMPLETES_FILENAME = "myco_incompletes.tsv"
MYCO_SOURCE_URL = "https://mycoportal.org/portal/collections/individual/index.php?occid="

keep_backup = True
last_mirrored = 0

MO_username = None
MO_API_key = None

iNat_username = None
iNat_password = None
iNat_JWT = None
iNat_JWT_timestamp = 0

tax_dict = {}
log = []

##### HELPERS
def calculate_MO_page_to_start(MOIDs, mirrorables):

    first_to_mirror = min(map(int, mirrorables))
    
    num_earlier_obses = len([num for num in map(int, MOIDs) if num < first_to_mirror])
    
    page = int(num_earlier_obses/OBSES_PER_MO_PAGE)+1 # +1 because MO pages start with 1
    
    return page
    
def mirroreds_pause():

    time_to_wait = INAT_UPDATE_INTERVAL - (time.time() - last_mirrored)
    if time_to_wait > 0:
        minutes = int(time_to_wait/60)
        minutes_part = str(minutes)+"m" if minutes > 0 else ""
        seconds = round(time_to_wait % 60)
        seconds_part = str(seconds)+"s"
        print("\nWaiting "+minutes_part+seconds_part+" to allow iNat database to reflect latest mirrored observations.")
        time.sleep(time_to_wait)
        
def calculate_radius(north, south, east, west):

    earth_circumference = 40042000
    earth_radius = 6371000

    ns_height = (abs(north-south)/360)*earth_circumference
    upper_ew_width = (abs(east-west)/360)*(math.sin(north)*earth_radius)
    lower_ew_width = (abs(east-west)/360)*(math.sin(south)*earth_radius)
    
    radius = max(ns_height, upper_ew_width, lower_ew_width)/2
    
    return radius
    
def complete_name(name, author):

    cn = name+" "+author if "sensu " in author else name # not " sensu " - remember author doesn't start with a space!
    
    return cn
    
def get_iNat_search_info(name):

    
    m_group = re.search(' group | group$', name)
    m_var = re.search(' var. ', name)
    m_subsp = re.search(' subsp. ', name)
    m_f = re.search(' f. ', name)
    m_sect = re.search(' sect. ',  name)
    m_subg = re.search(' subg. ', name)
    m_subsect = re.search(' subsect. ', name)
    
    if m_group:
        search_name = name[:m_group.start(0)]
        rank = "complex"
    elif m_var:
        search_name = name.replace(" var. ", " ")
        rank = "variety"
    elif m_subsp:
        search_name = name.replace(" subsp. ", " ")
        rank = "subspecies"
    elif m_f:
        search_name = name.replace(" f. ", " ")
        rank = "form"
    elif m_sect:
        search_name = name[m_sect.end(0):]
        rank = "section"
    elif m_subg:
        search_name = name[m_subg.end(0):]
        rank = "subgenus"
    elif m_subsect:
        search_name = name[m_subsect.end(0):]
        rank = "subsection"
    else:
        search_name = name
        rank = None
        
    return search_name, rank
 
def get_search_match(search_name, rank, search_results):
    
    for i in range(len(search_results)):
    
        if rank == None or (rank != None and search_results[i]["rank"] == rank):
            if search_results[i]["iconic_taxon_name"] in ("Fungi", "Protozoa"):
            
                taxon_ID = str(search_results[i]["id"])
            
                if search_results[i]["name"] == search_name:
                    match = True
                    
                else:
                    if search_results[i]["matched_term"] == search_name:
                        match = True
                    else:
                        match = False
                        
                if match:
                    return taxon_ID
            
    return None
 
def limit_name_italics(name):

    for word in ("group", "subg.", "sect.", "subsect.", "series", "subsp.", "var.", "f."):

        name = re.sub(" "+word+"( |$)", " </i>"+word+"<i>\\1", name)
        
    return name
 
def process_notes(notes):

    subbed_notes = re.sub("_(obs (\\d{1,6}))_", "<a href=\"https://www.mushroomobserver.org/\\2\">\\1</a>", notes)
    
    subbed_notes = re.sub("<em>([a-zA-Z\\. ]+)_/_([a-zA-Z\\. ]+)</em>", "<em>\\1</em>/<em>\\2</em>", subbed_notes)
    
    return subbed_notes

def prettify_date(datestr):

    # given e.g. "2012-12-28T04:22:34.000Z"
    
    m_date = re.match("(\\d{4})-(\\d{2})-(\\d{2})", datestr)
    
    if m_date:
        year = m_date.group(1)
        month = MONTHS[m_date.group(2)]
        day = str(int(m_date.group(3))) # str(int()) to remove leading 0s
        prettified = month + " " + day + ", " + year
        return prettified
        
    else:
        
        print("Failed to parse date \""+datestr+"\".")
        return "(unknown date)"

def clean_up_caption(caption, indent = 0):

    caption = re.sub("<\\s*/?\\s*p\\s*>", "", caption)
    caption = re.sub("<\\s*br\\s*/?\\s*>", "\n", caption)
    caption = re.sub("\n\n", "\n", caption)
    
    if "\n" in caption:
        caption = "\n"+caption
        caption = re.sub("\n", "\n"+"&nbsp;"*indent, caption)
    else:
        pass
    
    return caption

def current_date():

    today = date.today()
    
    year = today.strftime("%Y")
    month = today.strftime("%B")
    day = today.strftime("%d")
    

    return month+" "+str(int(day))+", "+year

def names_match(MO_name, iNat_name, iNat_rank):

    MO_words = MO_name.split(" ")

    if iNat_rank == "complex":
        return MO_name == iNat_name + " group"
        
    elif iNat_rank in BINOMIAL_RANKS:
        return len(MO_words) == 3 and MO_words[-2] == BINOMIAL_RANKS[iNat_rank] and MO_words[-1] == iNat_name
        
    elif iNat_rank in TRINOMIAL_RANKS:
        return len(MO_words) == 4 and MO_words[-2] == TRINOMIAL_RANKS[iNat_rank] and " ".join((MO_words[0], MO_words[1], MO_words[3])) == iNat_name
        
    else:
        return MO_name == iNat_name

##### LOADERS
def LOAD_settings():

    global keep_backup
    global last_mirrored
    
    global MO_username
    global MO_API_key
    
    global iNat_username
    global iNat_password
    global iNat_JWT

    settings = {}

    if os.path.isfile(SETTINGS_FILENAME):
        lines = open(SETTINGS_FILENAME, encoding = "utf8").read().split("\n")
    else:
        lines = ""
        
    for line in lines:
        splat = line.split("\t")
        if len(splat) == 2 and len(splat[1].strip()) > 0:
            settings[splat[0]] = splat[1].strip()
        
    if "keep backup" in settings:
        if settings["keep backup"].strip().lower() == "true":
            keep_backup = True
        elif settings["keep backup"].strip().lower() == "false":
            keep_backup = False
            
    if "last mirrored" in settings:
        last_mirrored = float(settings["last mirrored"])
            
    if "MO username" in settings:
        MO_username = settings["MO username"]
        
    if "iNat username" in settings:
        iNat_username = settings["iNat username"]
        
    if "iNat password" in settings:
        iNat_password = settings["iNat password"]
        
    if "iNat JWT" in settings:
        iNat_JWT = settings["iNat JWT"]
        
    if "iNat JWT timestamp" in settings:
        iNat_JWT_timestamp = float(settings["iNat JWT timestamp"])
            
    if "MO API key" in settings:
        MO_API_key = settings["MO API key"]
            
    return settings

def LOAD_taxon_dictionary():

    global tax_dict

    with open(TAXON_DICTIONARY_FILENAME, encoding="utf8") as infile:
        lines = infile.read().split("\n")
        
    for line in lines:
        if "\t" not in line:
            continue
        splat = line.split("\t")
        MO_name = splat[0]
        iNat_ID = splat[1]
        tax_dict[MO_name] = iNat_ID

def LOAD_log():

    global log

    with open(LOG_FILENAME, encoding = "utf8") as infile:
        lines = infile.read().split("\n")
        
    log = [line for line in lines if len(line) > 0]

def LOAD_mirroreds():

    if os.path.isfile(COMPLETES_FILENAME):
        lines = open(COMPLETES_FILENAME, encoding="utf8").read().split("\n")
        already_mirrored_MOIDs = [line.strip() for line in lines if len(line.strip()) > 0]
    else:
        already_mirrored_MOIDs = []
        
    return already_mirrored_MOIDs

def LOAD_incompletes():
    
    if os.path.isfile(INCOMPLETES_FILENAME):
        with open(INCOMPLETES_FILENAME, encoding="utf8") as inf:
            iNatIDs = [line.strip() for line in inf.read().split("\n")]
            iNatIDs = [iNatID for iNatID in iNatIDs if iNatID != ""]
    else:
        iNatIDs = []

    return iNatIDs

##### INPUTTERS
def INPUT_yes_or_no():

    while True:
    
        inp = input("YES/NO: ")
        
        if inp.strip().lower() in ("yes", "y"):
            return True
        elif inp.strip().lower() in ("no", "n"):
            return False
        else:
            print("Enter \"yes\" or \"no\".")

def INPUT_login(platform):

    if platform == "MO":
        #print("\nEnter your Mushroom Observer login information.")
        pass
    elif platform == "iNat":
        #print("\nEnter your iNaturalist login information.")
        pass
    else:
        print("Invalid platform \""+platform+"\". Quitting.\n")

    valid_username = False
    while not valid_username:
    
        if platform == "MO":
            username = input("\nMushroom Observer username: ")
        elif platform == "iNat":
            username = input("\niNaturalist username: ")
        
        if username != "":
            valid_username = True
    
    if platform in ("MO", "iNat"):
        return username
    
    valid_password = False
    while not valid_password:
    
        password = input("password: ")
        
        if password != "":
            valid_password = True
            
    return username, password
            
def INPUT_num_to_mirror(num_mirrorable):

    while True:

        inp = input("How many of these would you like to mirror now? ")
        
        try:
            inp_num = int(inp)
            
        except:
            print("\nEnter an integer.")
            
        else:
        
            if inp_num < 0 or inp_num > num_mirrorable:
                print("\nEnter an integer between 0 and "+str(num_mirrorable)+".")
            else:
                return inp_num

def INPUT_allow_quit():

    print("\nPress ctrl+c to safely quit early. Continuing shortly.")
    
    try:
        time.sleep(QUIT_PAUSE_TIME)
    except KeyboardInterrupt:
        print("\nProgram has been safely stopped.")
        input("Press enter to exit.")
        print("Goodbye.\n")
        exit(0)
    
    return
    
def INPUT_pole():

    while True:
    
        inp = input("Change/Approximate/Skip: ")
        
        if inp.strip().lower() in ("change", "c"):
            return "c"
        elif inp.strip().lower() in ("approximate", "approx", "a"):
            return "a"
        elif inp.strip().lower() in ("skip", "s"):
            return "s"
        else:
            print("Enter \"c\", \"a\", or \"s\".")
    
##### MAJORS
def update_settings(entries):

    old_settings = LOAD_settings()
    
    new_settings = old_settings | entries
    
    to_write = "\n".join([key+"\t"+new_settings[key] for key in new_settings])
    
    open(SETTINGS_FILENAME, "w", encoding = "utf8").write(to_write)

def set_MO_info():

    global MO_username
    global MO_API_key
    
    if (not MO_username) or len(MO_username) == 0:
        MO_username = INPUT_login("MO")

    need_new_key = True
    if MO_API_key and len(MO_API_key) > 0:
        if mo_api.confirm_API_key(MO_API_key, MO_username):
            need_new_key = False
            print("\nSaved MO username and API key worked.")
        else:
            pass
                
    if need_new_key:
    
        print("\nWe need an API key from MO.")
        MO_API_key = mo_api.get_API_key(MO_username)
        
    update_settings({"MO username" : MO_username, "MO API key" : MO_API_key})

def set_iNat_info(force_reset = False):

    global iNat_username
    #global iNat_password
    global iNat_JWT
    global iNat_JWT_timestamp
    
    
    if not iNat_username:
        iNat_username = INPUT_login("iNat")
        update_settings({"iNat username" : iNat_username})
        
    if iNat_JWT and not force_reset:
        if inat_api.confirm_JWT(iNat_JWT, iNat_username):
            print("\nSaved iNat username and JWT worked.")
            return
    
    print("\nWe need a JWT from iNat.")
    
    try_number = 0
    while True:
    
        if try_number > 0:
            print("Try again.")
            iNat_username = INPUT_login("iNat")
            update_settings({"iNat username" : iNat_username})
            
        iNat_JWT = inat_api.get_JWT_PKCE()
        if inat_api.confirm_JWT(iNat_JWT, iNat_username):
            iNat_JWT_timestamp = time.time()
            update_settings({"iNat JWT" : iNat_JWT, "iNat JWT timestamp" : str(iNat_JWT_timestamp)})
            break
        else:
            print("Didn't work.")
            
        try_number += 1
        
def add_to_log(entry):

    global log
    
    log.append(entry)
    
    with open(LOG_FILENAME, "w", encoding = "utf8") as outfile:
        outfile.write("\n".join(log))

def update_completes(MOIDs, appending):

    with open(COMPLETES_FILENAME, encoding="utf8") as inf:
        file_empty = (len(inf.read()) == 0)

    if appending:
    
        with open(COMPLETES_FILENAME, "a", encoding = "utf8") as outf:
            if file_empty:
                outf.write("\n".join(MOIDs))
            else:
                outf.write("\n"+"\n".join(MOIDs))
    
    else:
    
        with open(COMPLETES_FILENAME, "w", encoding="utf8") as outf:
            outf.write("\n".join(MOIDs))

def update_incompletes(iNatID, adding):

    with open(INCOMPLETES_FILENAME, encoding="utf8") as inf:
        file_empty = (len(inf.read()) == 0)

    if adding:
        with open(INCOMPLETES_FILENAME, "a", encoding="utf8") as outf:
            if file_empty:
                outf.write(iNatID)
            else:
                outf.write("\n"+iNatID)
            
    else:
    
        old_iNatIDs = LOAD_incompletes()
        new_iNatIDs = [o for o in old_iNatIDs if o != iNatID]
            
        with open(INCOMPLETES_FILENAME, "w", encoding="utf8") as outf:
            outf.write("\n".join(new_iNatIDs))
            
def deal_with_incompletes():

    incompletes = LOAD_incompletes()
    
    if len(incompletes) > 0:
    
        numchunk = str(len(incompletes))+" incompletely mirrored observations" if len(incompletes) != 1 else "1 incompletely mirrored observation"
        numarticle = "them" if len(incompletes) != 1 else "it"
        print("\nLocal file indicates this program has left "+numchunk+" on iNat. Delete "+numarticle+"? (Recommended)")
        
        delete_incompletes = INPUT_yes_or_no()
        
        if delete_incompletes:
        
            print("Are you SURE you want to delete "+numarticle+"? (Still recommended)")
            delete_for_sure = INPUT_yes_or_no()
            
            if delete_for_sure:
                print("Deleting...")
                for incomplete in incompletes:
                    inat_api.delete_observation(incomplete, iNat_JWT)
                    update_incompletes(incomplete, False)
                print("Finished deleting.")

def get_already_mirrored_MOIDs():

    already_mirrored_MOIDs = LOAD_mirroreds()

    need_to_check_iNat = False
    
    if len(already_mirrored_MOIDs) == 0:
        need_to_check_iNat = True
    else:
        number_chunk = str(len(already_mirrored_MOIDs))+" MO observations" if len(already_mirrored_MOIDs) != 1 else "1 MO observation"
        print("\nLocal file indicates "+number_chunk+" already mirrored on iNat.")
        print("\nRecheck iNat for mirrored observations? This takes some time. It is only necessary if: ")
        print("\t- you have been adding/removing mirrored observations on iNat manually, with another program, or with this program run from another folder,")
        print("\t- you (may) have manually modified \"completes.txt\" in this folder,")
        print("\t- OR you last ran this program for another user.")
        need_to_check_iNat = INPUT_yes_or_no()
            
    if need_to_check_iNat:
        mirroreds_pause()
        print("\nGetting IDs of your MO observations already mirrored on iNat. This will take some time.")
        already_mirrored_MOIDs = inat_api.get_mirrored_MOIDs(iNat_username)
        print("Got "+str(len(already_mirrored_MOIDs))+" IDs.")
        update_completes(already_mirrored_MOIDs, False)
        
    return set(already_mirrored_MOIDs)

def backup_results(parsed):

    if not os.path.isdir(BACKUP_FOLDER):
        os.mkdir(BACKUP_FOLDER)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    filepath = BACKUP_FOLDER+"/"+timestamp+".json"

    to_write = json.dumps(parsed, indent = 4, sort_keys = False)

    open(filepath, "w", encoding="utf8").write(to_write)
    
def determine_taxon_ID(MO_obs, MO_name):
    
    ### tier 0: name is already in dictionary
    if MO_name in tax_dict:
        iNat_name_ID = tax_dict[MO_name]
        if inat_api.name_ID_exists(iNat_name_ID):
            return tax_dict[MO_name]
        else:
            print("Name ID "+iNat_name_ID+" found in dictionary but not found on iNat. Searching for \""+MO_name+"\" on iNat.")
            add_to_log("name ID not on iNat" + "\t" + iNat_name_ID)
    else:
        print("Name \""+MO_name+"\" is not in the local dictionary. Searching for it on iNat.")   
        add_to_log("name not in dictionary" + "\t" + MO_name)
        
    search_name, rank = get_iNat_search_info(MO_name)
    search_results = inat_api.search_for_name(search_name)
    perfect_match_ID = get_search_match(search_name, rank, search_results)
    
    ### tier 1: found perfect match
    if perfect_match_ID != None:
        print("Found "+MO_name+".")
        return perfect_match_ID
        
    else:
    
        if " " in search_name:
            search_genoid = search_name.split(" ")[0]
            print("Not found. Searching for \""+search_genoid+"\" instead.")
            search_results = inat_api.search_for_name(search_genoid)
            genoid_match_ID = get_search_match(search_genoid, None, search_results)
            
            ### tier 2: found genoid match
            if genoid_match_ID != None:
                print("Found.")
                return genoid_match_ID
                
            ### tier 3: default to Life
            else:
                print("Could not find name \""+search_genoid+"\" on iNat. Defaulting to taxon \"Life\".")
                return "48460" ## Life
                
        ### genoid is no different, so tier 3: default to Life
        else:
            print("Could not find name \""+MO_name+"\" on iNat. Defaulting to taxon \"Life\".")
            return "48460" ## Life      

def process_specimens_for_fields(MO_obs):

    MOID = str(MO_obs["id"])

    specimen_fields = {}

    if "herbarium_records" in MO_obs:
        herbaria = [hr["herbarium"]["name"] for hr in MO_obs["herbarium_records"]]
        accessions = [(hr["accession_number"] if "accession_number" in hr else "s.n.") for hr in MO_obs["herbarium_records"]]
    else:
        herbaria = []
        accessions = []
    
    unfielded_specimens = []
    if len(herbaria) == len(accessions):
    
        if len(herbaria) > 0:
            specimen_fields["1162"] = "Yes" ## "Voucher Specimen Taken"
            
        for i in range(len(herbaria)):
            if re.search("\\("+MO_username+"\\): Personal Herbarium", herbaria[i], flags = re.IGNORECASE):
                if "7627" not in specimen_fields:
                    specimen_fields["7627"] = accessions[i]
                else:
                    unfielded_specimens.append(("Personal Herbarium ("+MO_username+")", accessions[i]))
            else:
                if "9539" not in specimen_fields:
                    specimen_fields["9539"] = herbaria[i] ## "Herbarium Name"
                    specimen_fields["9540"] = accessions[i] ## "Herbarium Catalog Number"
                else:
                    unfielded_specimens.append((herbaria[i], accessions[i]))
    else:
        print("Mismatched herbaria and accessions for MO obs "+MOID+".")
        
    return specimen_fields, unfielded_specimens
    
def process_sequences_for_fields(MO_obs):

    MOID = str(MO_obs["id"])

    sequence_fields = {}
    unfielded_sequences = []
    fielded_sequences = []

    if "sequences" in MO_obs:
        sequence_loci = [seq["locus"] for seq in MO_obs["sequences"]]
        sequence_bases = [seq["bases"] if "bases" in seq else None for seq in MO_obs["sequences"]]
        sequence_deposits = [(seq["archive"], seq["accession"]) if "archive" in seq else (None, None) for seq in MO_obs["sequences"]]
        
        sequence_notes = []
        for seq in MO_obs["sequences"]:
            if "notes" in seq:
                stripped_note = seq["notes"].strip()
                if len(stripped_note) == 0: sequence_notes.append(None)
                elif len(stripped_note) > 0: sequence_notes.append(stripped_note)
            else:
                sequence_notes.append(None)

    else:
        sequence_loci = []
        sequence_bases = []
        sequence_deposits = []
        sequence_notes = []
    
    if len(sequence_loci) == len(sequence_bases):
    
        for i in range(len(sequence_loci)):
        
            locus = sequence_loci[i]
            bases = sequence_bases[i]
            notes = clean_up_caption(sequence_notes[i]) if sequence_notes[i] else None
            genbank_accession = sequence_deposits[i][1] if sequence_deposits[i][0] == "GenBank" else None
            
            if re.match("its", locus, flags = re.IGNORECASE):
                locus_ID = "2330" ## "DNA Barcode ITS"
            elif re.match("lsu|28s", locus, flags = re.IGNORECASE):
                locus_ID = "14524" ## "DNA Barcode LSU"
            elif re.match("rpb2", locus, flags = re.IGNORECASE):
                locus_ID = "14019" ## "DNA Barcode RPB2"
            elif re.match("ssu|18s", locus, flags = re.IGNORECASE):
                locus_ID = "14900" ## "DNA Barcode 18S"
            elif re.match("tef1|tef-1", locus, flags = re.IGNORECASE):
                locus_ID = "14901" ## "DNA Barcode TEF1"
            else:
                locus_ID = None
                
                
            # fielding sequence
            if bases != None and locus_ID != None and locus_ID not in sequence_fields:
                sequence_fields[locus_ID] = bases
                fielded_sequences.append((locus, bases, genbank_accession, notes))
                
            # not fielding sequence
            else:
                unfielded_sequences.append((locus, bases, genbank_accession, notes))
                
                
            # fielding GenBank accession independently from sequence
            if sequence_deposits[i][0] == "GenBank":
                sequence_fields["7555"] = sequence_deposits[i][1]
            
    else:
        print("Mismatched sequence loci and bases for obs "+MOID+".")
        
    return sequence_fields, unfielded_sequences, fielded_sequences

def build_creation_obj(MO_obs):

    params = {}

    MOID = str(MO_obs["id"])
    
    name = MO_obs["consensus"]["name"]
    author = MO_obs["consensus"]["author"] if "author" in MO_obs["consensus"] else ""
    MO_name = complete_name(name, author)
    
    action = None # default, as opposed to retry or skip
    
    ########## BASIC ##########
    
    params["taxon_id"] = determine_taxon_ID(MO_obs, MO_name)
    params["observed_on_string"] = MO_obs["date"]
    params["place_guess"] = MO_obs["location"]["name"]
    params["description"] = "This observation is incompletely mirrored from Mushroom Observer. Refresh soon to see the full observation."
    
    if ("latitude" in MO_obs and "longitude" in MO_obs) \
    and (MO_obs["latitude"] and MO_obs["longitude"]):
        params["latitude"] = MO_obs["latitude"]
        params["longitude"] = MO_obs["longitude"]
        params["positional_accuracy"] = 20
    else:
        north = float(MO_obs["location"]["latitude_north"])
        south = float(MO_obs["location"]["latitude_south"])
        east = float(MO_obs["location"]["longitude_east"])
        west = float(MO_obs["location"]["longitude_west"])
        params["latitude"] = (north+south)/2
        params["longitude"] = (east+west)/2
        params["positional_accuracy"] = calculate_radius(north, south, east, west)
        
    if float(params["latitude"]) == -90:
        pole = "the south pole"
        acceptable_latitude = -89.99999
        acceptable_longitude = float(params["longitude"])
    elif float(params["latitude"]) == 90:
        pole = "the north pole"
        acceptable_latitude = 89.99999
        acceptable_longitude = float(params["longitude"])
    elif float(params["latitude"]) == 0 and float(params["longitude"]) == 0:
        pole = "0°N, 0°E"
        acceptable_latitude = 0.00001
        acceptable_longitude = 0.00001
    else:
        pole = None
    if pole != None:
        print("Your MO observation coordinates are set to "+pole+". iNat does not allow this. Would you like to:")
        print("- manually change (c) the obs and allow this program to retry")
        print("- continue posting the obs with latitude approximated (a) as "+str(acceptable_latitude)+", "+str(acceptable_longitude))
        print("- or skip (s) this obs?")
        user_choice = INPUT_pole()
        if user_choice == "c":
            while True:
                print("Have you changed the MO observation coordinates?")
                changed = INPUT_yes_or_no()
                if changed:
                    break
            return None, None, "RETRY"
        elif user_choice == "a":
            params["latitude"] = str(acceptable_latitude)
        elif user_choice == "s":
            return None, None, "SKIP"
        
    if "gps_hidden" in MO_obs and str(MO_obs["gps_hidden"]).lower() == "true":
        params["geoprivacy"] = "obscured"
    else:
        params["geoprivacy"] = "open"
            
    
    obj = json.dumps({"observation" : params})
    
    return obj, MO_name, action

def get_fields(MO_obs):

    MOID = str(MO_obs["id"])

    fields = {}
    
    fields["5005"] = "http://mushroomobserver.org/"+MOID ## "Mushroom Observer URL"
    
    if "collection_numbers" in MO_obs and len(MO_obs["collection_numbers"]) > 0:
        fields["7617"] = MO_obs["collection_numbers"][0]["number"]
        
    specimen_fields, unfielded_specimens = process_specimens_for_fields(MO_obs)
    sequence_fields, unfielded_sequences, fielded_sequences = process_sequences_for_fields(MO_obs)
    fields = fields | specimen_fields | sequence_fields
    
    return fields, unfielded_specimens, unfielded_sequences, fielded_sequences

def build_proposal_string(MO_obs):

    if "namings" in MO_obs:
    
        proposal_pairs = []
        
        for naming in MO_obs["namings"]:
        
            naming_ID = naming["id"]
            
            proposal_line_list = []
            name_part = "<i>"+limit_name_italics(naming["name"]["name"])+"</i>"
            author_part = naming["name"]["author"] if "author" in naming["name"] and "sensu" in naming["name"]["author"] else None
            complete_name_part = name_part+" "+author_part if author_part else name_part
            proposal_line_list.append("<b>"+complete_name_part+"</b> proposed by "+naming["owner"]["login_name"])
            
            justification_line_list = []
            justifications = naming["reasons"] if "reasons" in naming else []
            for justification in justifications:
                if justification["reason"] == "Recognized by sight" and justification["notes"] == "":
                    continue
                justification_line_list.append("&#8226; "+justification["reason"]+": "+process_notes(clean_up_caption(justification["notes"], 3)))
            proposal_line_list += justification_line_list
            
            for vote in MO_obs["votes"]:
                if vote["naming_id"] == naming_ID:
                    vote_owner = "anonymous" if vote["owner"] == "anonymous" else vote["owner"]["login_name"]
                    proposal_line_list.append(vote_owner+": "+CONFIDENCES[int(vote["confidence"])])
                    
            confidence = float(naming["confidence"])*100/3
            
            proposal_line_list.append(" = MO community vote "+str(round(confidence))+"%")
            proposal_string = "\n".join(proposal_line_list)
            
            proposal_pairs.append((proposal_string, confidence))
        
        proposal_pairs.sort(key = lambda x : x[1], reverse = True)
            
        final_string = "\n\n".join([pp[0] for pp in proposal_pairs])

    else:
        final_string = ""
        
    return final_string
    
def build_proposal_obj(iNatID, taxon_ID, MO_obs):

    params = {}
    params["observation_id"] = int(iNatID)
    params["taxon_id"] = int(taxon_ID) if taxon_ID != None else 48460 ## Life
    params["current"] = True
    params["body"] = build_proposal_string(MO_obs)
    
    obj = json.dumps({"identification" : params})
    
    return obj
    
def get_image_data(MO_obs):

    MOID = str(MO_obs["id"])
    
    print("Downloading images from MO. This may take some time.")
    
    images = []
    if "primary_image" in MO_obs:
        images.append(MO_obs["primary_image"])
    if "images" in MO_obs:
        images += MO_obs["images"]
        
    image_captions = [im["notes"] if "notes" in im else "" for im in images]
    image_locations = [im["original_url"] for im in images]
    image_locations = [re.sub("\\.raw$", "\\.jpg$", loc, flags=re.IGNORECASE) for loc in image_locations] # workaround for .JPG images on MO that somehow were uploaded with .RAW extension
    image_data = mo_api.get_images(image_locations)
        
    return image_data, image_captions

def post_images(iNatID, image_data):

    num_chunk = str(len(image_data))+" images" if len(image_data) != 1 else "1 image"
    print("Got "+num_chunk+".")
    
    print("Uploading images to iNat. This may take some time.")
    
    for i in range(len(image_data)):
    
        print("Uploading image #"+str(i+1)+".")
        image = image_data[i]
        inat_api.post_image(iNatID, image, iNat_JWT)
        
    print("Done uploading images.")
    
def build_note_obj(MO_obs, image_captions, unfielded_specimens, unfielded_sequences, fielded_sequences):

    if "notes" in MO_obs:
        note_from_MO = process_notes(MO_obs["notes"])
    else:
        note_from_MO = ""
        

    image_captions = [clean_up_caption(pc) for pc in image_captions]
    note_image_captions = "\n".join(["Image #"+str(i+1)+": "+image_captions[i] for i in range(len(image_captions)) if image_captions[i] != ""])
    
    note_unfielded_specimens = "Additional specimens not added to iNat observation fields:\n"+"\n".join([us[0]+": "+us[1] for us in unfielded_specimens]) if len(unfielded_specimens) > 0 else ""
    
    note_fielded_sequence_parts = []
    for seq in fielded_sequences:
        genbank_part = "<a href=\"https://www.ncbi.nlm.nih.gov/nuccore/"+seq[2]+"\">GenBank "+seq[2]+"</a>" if seq[2] else None
        seqnotes_part = seq[3]
        combined_part = ". ".join(p for p in [genbank_part, seqnotes_part] if p)
        if combined_part != "": note_fielded_sequence_parts.append(seq[0]+": "+combined_part)
    note_fielded_sequences = "Additional notes for sequences (bases on the right):\n\n"+"\n".join(note_fielded_sequence_parts) if len(note_fielded_sequence_parts) > 0 else ""
    
    note_unfielded_sequence_parts = []
    for seq in unfielded_sequences:
        bases_part = seq[1] if seq[1] != None else ""
        genbank_part = "<a href=\"https://www.ncbi.nlm.nih.gov/nuccore/"+seq[2]+"\">GenBank "+seq[2]+"</a>" if seq[2] else None
        seqnotes_part = seq[3]
        note_unfielded_sequence_parts.append(seq[0]+": "+". ".join(p for p in [genbank_part, seqnotes_part] if p)+"\n"+bases_part)
    note_unfielded_sequences = "Additional sequences:\n\n"+"\n\n".join(note_unfielded_sequence_parts) if len(note_unfielded_sequence_parts) > 0 else ""
    
    note_originally_posted = "Originally posted to Mushroom Observer on "+prettify_date(MO_obs["created_at"])+"."
    
    full_notes = "\n\n&#8212;\n\n".join([n for n in (note_from_MO, note_image_captions, note_unfielded_specimens, note_fielded_sequences, note_unfielded_sequences, note_originally_posted) if len(n) > 0])
    
    params = {"description" : full_notes}
    obj = json.dumps({"observation" : params, "ignore_photos" : "1"}) # ignore_photos is CRUCIAL
    
    return obj
    
def mirror_wrapper(num_to_mirror, already_mirrored_MOIDs, MO_page_to_start, MOIDs_to_mirror):

    print("\nPreparing...")
    print("Loading local files.")
    LOAD_taxon_dictionary()
    LOAD_log()

    mirrored_MOIDs = set([])
    MO_page = MO_page_to_start
    print("Starting at page "+str(MO_page))
    
    while len(mirrored_MOIDs) < num_to_mirror:
    
        print("\nCollecting some full observations from MO.")
        
        ####
        available_MO_obses = mo_api.get_full_obses(MO_username, MO_page, MO_API_key)
        print("Got "+str(len(available_MO_obses))+" obses.")
        #available_MO_obses = [mo_api.get_single_obs(MO_username, MOIDs_to_mirror.pop(0), MO_API_key)]
        ####
        
        if len(available_MO_obses) == 0:
            print("\nNo more obses left from MO. Hopefully your mirroring is complete.")
            input("Press enter to exit.")
            print("Goodbye.\n")
            exit(0)
        if keep_backup:
            backup_results(available_MO_obses)
            
        moobs_index = 0
        action = "NONE"
        
        while moobs_index < len(available_MO_obses):
    
            MO_obs = available_MO_obses[moobs_index]
            
            MOID = str(MO_obs["id"])
            
            if MOID in already_mirrored_MOIDs:
                #print(MOID+" already mirrored, skipping")
                moobs_index += 1
                continue
                
            print("\nMirroring MO observation "+MOID+"...")
            
            JWT_age = time.time() - iNat_JWT_timestamp
            JWT_time_left =  max(INAT_JWT_LIFESPAN - JWT_age, 0)
            if JWT_time_left <= MAX_EXPECTED_MIRROR_TIME:
                set_iNat_info(True)
            
            ### SKELETON ###
            print("Creating skeleton observation.")
            creation_obj, MO_name, action = build_creation_obj(MO_obs)
            if action == "RETRY":
                break
            elif action == "SKIP":
                moobs_index += 1
                continue
            iNatID = inat_api.create_obs(creation_obj, iNat_JWT)
            update_incompletes(iNatID, True)
            print("Successfully created skeleton observation. iNat ID is "+iNatID+".")
            
            ### FIELDS ###
            print("Filling in observation fields.")
            fields, unfielded_specimens, unfielded_sequences, fielded_sequences = get_fields(MO_obs)
            inat_api.post_fields(iNatID, fields, iNat_JWT)
            print("Finished observation fields.")
            
            ### PROPOSAL ###            
            print("Updating name proposal on the new observation.")
            identification_ID, taxon_ID, iNat_name, iNat_rank = inat_api.get_existing_proposal(iNatID)
            proposal_obj = build_proposal_obj(iNatID, taxon_ID, MO_obs)
            inat_api.update_proposal(identification_ID, proposal_obj, iNat_JWT)
            print("Updated.")
            
            # see if names match #
            if not names_match(MO_name, iNat_name, iNat_rank):
                add_to_log("name mismatch" + "\t" + MO_name + "\t" + iNat_name+" ("+iNat_rank+")" + "\t" + MOID + "\t" + iNatID)
                
            ### IMAGES ###
            image_data, image_captions = get_image_data(MO_obs)
            post_images(iNatID, image_data)
            
            ### NOTES ###
            print("Filling in observation notes.")
            note_obj = build_note_obj(MO_obs, image_captions, unfielded_specimens, unfielded_sequences, fielded_sequences)
            inat_api.update_obs(iNatID, note_obj, iNat_JWT)
            
            ### UPDATE MO ###
            print("Linking to new iNat observation from original MO observation.")
            mo_api.add_link(MO_username, MOID, iNatID, current_date(), MO_API_key)
            
            # record locally #
            update_settings({"last mirrored" : str(time.time())})
            update_completes([MOID], True)
            update_incompletes(iNatID, False)
            
            mirrored_MOIDs.add(MOID)
            print("Done mirroring observation "+str(len(mirrored_MOIDs))+" of "+str(num_to_mirror)+".")
            if len(mirrored_MOIDs) >= num_to_mirror:
                break
                
            INPUT_allow_quit()
            
            moobs_index += 1
        
        if action != "RETRY":
            MO_page += 1
        
    return mirrored_MOIDs

##### MYCOPORTAL HELPERS

def extract_occid(token):

    token = token.strip()

    if token.isdigit():
        return token

    try:
        parsed = urlparse(token)
        qs = parse_qs(parsed.query)
        if "occid" in qs:
            val = qs["occid"][0]
            if val.isdigit():
                return val
    except Exception:
        pass

    return None

def parse_myco_occids(args):

    raw_tokens = []

    if args.myco is not None:
        raw_tokens += args.myco

    if args.myco_csv is not None:
        try:
            with open(args.myco_csv, encoding="utf8") as f:
                reader = csv.DictReader(f)
                # find occid column (case-insensitive)
                occid_col = None
                if reader.fieldnames:
                    for fn in reader.fieldnames:
                        if fn.strip().lower() == "occid":
                            occid_col = fn
                            break

                if occid_col is not None:
                    for row in reader:
                        val = row[occid_col].strip()
                        if val:
                            raw_tokens.append(val)
                else:
                    # no occid header — reread using first column, skip header if non-numeric
                    f.seek(0)
                    csv_reader = csv.reader(f)
                    for i, row in enumerate(csv_reader):
                        if len(row) > 0:
                            val = row[0].strip()
                            if val:
                                if i == 0 and not val.isdigit():
                                    continue
                                raw_tokens.append(val)
        except FileNotFoundError:
            print("CSV file not found: " + args.myco_csv)
        except Exception as e:
            print("Error reading CSV file: " + repr(e))

    seen = set()
    occids = []
    for token in raw_tokens:
        oid = extract_occid(token)
        if oid is None:
            print("Warning: skipping non-numeric token: " + token)
            continue
        if oid not in seen:
            seen.add(oid)
            occids.append(oid)

    return occids

def resolve_myco_field(occ, *keys):

    for key in keys:
        val = occ.get(key)
        if val is not None and val != "":
            return val
    return None

def normalize_myco_date(raw_date):

    if raw_date is None:
        return (None, None)

    raw_date = str(raw_date).strip()
    if raw_date == "":
        return (None, None)

    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw_date)
    if m:
        year, month, day = m.group(1), m.group(2), m.group(3)
        if day == "00" and month == "00":
            return (year, raw_date)
        elif day == "00":
            return (year + "-" + month, raw_date)
        else:
            return (raw_date, None)

    # already just YYYY or YYYY-MM
    if re.match(r"^\d{4}$", raw_date):
        return (raw_date, None)
    if re.match(r"^\d{4}-\d{2}$", raw_date):
        return (raw_date, None)

    # anything else — don't send to iNat, just record verbatim in notes
    return (None, raw_date)

##### MYCOPORTAL TRACKING

def load_myco_completes():

    if os.path.isfile(MYCO_COMPLETES_FILENAME):
        lines = open(MYCO_COMPLETES_FILENAME, encoding="utf8").read().split("\n")
        return set(line.strip() for line in lines if line.strip())
    else:
        open(MYCO_COMPLETES_FILENAME, "w", encoding="utf8").write("")
        return set()

def update_myco_completes(occid):

    existing = load_myco_completes()
    if occid in existing:
        return
    file_size = os.path.getsize(MYCO_COMPLETES_FILENAME) if os.path.isfile(MYCO_COMPLETES_FILENAME) else 0
    with open(MYCO_COMPLETES_FILENAME, "a", encoding="utf8") as f:
        if file_size == 0:
            f.write(occid)
        else:
            f.write("\n" + occid)

MYCO_INCOMPLETES_HEADER = "occid\tiNatID\tstage"

def load_myco_incompletes():

    if not os.path.isfile(MYCO_INCOMPLETES_FILENAME):
        open(MYCO_INCOMPLETES_FILENAME, "w", encoding="utf8").write(MYCO_INCOMPLETES_HEADER + "\n")
        return []

    rows = []
    with open(MYCO_INCOMPLETES_FILENAME, encoding="utf8") as f:
        for line in f:
            line = line.strip()
            if not line or line == MYCO_INCOMPLETES_HEADER:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                rows.append({"occid": parts[0], "iNatID": parts[1], "stage": parts[2]})

    return rows

def update_myco_incomplete(occid, iNatID, stage):

    rows = load_myco_incompletes()
    found = False
    for row in rows:
        if row["occid"] == occid:
            row["iNatID"] = iNatID
            row["stage"] = stage
            found = True
            break
    if not found:
        rows.append({"occid": occid, "iNatID": iNatID, "stage": stage})

    with open(MYCO_INCOMPLETES_FILENAME, "w", encoding="utf8") as f:
        f.write(MYCO_INCOMPLETES_HEADER + "\n")
        f.write("\n".join(row["occid"] + "\t" + row["iNatID"] + "\t" + row["stage"] for row in rows))

def remove_myco_incomplete(occid):

    rows = load_myco_incompletes()
    rows = [row for row in rows if row["occid"] != occid]

    with open(MYCO_INCOMPLETES_FILENAME, "w", encoding="utf8") as f:
        f.write(MYCO_INCOMPLETES_HEADER + "\n")
        f.write("\n".join(row["occid"] + "\t" + row["iNatID"] + "\t" + row["stage"] for row in rows))

def deal_with_myco_incompletes():

    incompletes = load_myco_incompletes()

    if len(incompletes) == 0:
        return

    numchunk = str(len(incompletes)) + " incompletely imported observations" if len(incompletes) != 1 else "1 incompletely imported observation"
    numarticle = "them" if len(incompletes) != 1 else "it"
    print("\nLocal file indicates this program has left " + numchunk + " on iNat from MycoPortal imports.")
    for row in incompletes:
        print("  occid " + row["occid"] + " -> iNat " + row["iNatID"] + " (stage: " + row["stage"] + ")")
    print("Delete " + numarticle + "? (Recommended)")

    delete_incompletes = INPUT_yes_or_no()

    if delete_incompletes:

        print("Are you SURE you want to delete " + numarticle + "? (Still recommended)")
        delete_for_sure = INPUT_yes_or_no()

        if delete_for_sure:
            print("Deleting...")
            for row in incompletes:
                inat_api.delete_observation(row["iNatID"], iNat_JWT)
            # rewrite file once with all deleted rows removed
            with open(MYCO_INCOMPLETES_FILENAME, "w", encoding="utf8") as f:
                f.write(MYCO_INCOMPLETES_HEADER + "\n")
            print("Finished deleting.")

##### MYCOPORTAL -> INAT MAPPING

def build_myco_creation_obj(occurrence, occid, args):

    params = {}

    # taxon
    sciname = resolve_myco_field(occurrence, "sciname", "scientificName")
    display_name = sciname if sciname else "Unknown"

    if sciname:
        search_name, rank = get_iNat_search_info(sciname)
        search_results = inat_api.search_for_name(search_name)
        perfect_match_ID = get_search_match(search_name, rank, search_results)

        if perfect_match_ID is not None:
            params["taxon_id"] = perfect_match_ID
        elif " " in search_name:
            search_genoid = search_name.split(" ")[0]
            print("Not found. Searching for \"" + search_genoid + "\" instead.")
            search_results = inat_api.search_for_name(search_genoid)
            genoid_match_ID = get_search_match(search_genoid, None, search_results)
            if genoid_match_ID is not None:
                params["taxon_id"] = genoid_match_ID
            else:
                print("Could not find name \"" + search_genoid + "\" on iNat. Defaulting to taxon \"Life\".")
                params["taxon_id"] = "48460"
        else:
            print("Could not find name \"" + sciname + "\" on iNat. Defaulting to taxon \"Life\".")
            params["taxon_id"] = "48460"
    else:
        params["taxon_id"] = "48460"

    # date
    raw_date = resolve_myco_field(occurrence, "eventDate", "date")
    observed_on_string, verbatim_date_note = normalize_myco_date(raw_date)
    if observed_on_string:
        params["observed_on_string"] = observed_on_string

    # place guess
    place_parts = []
    for key in ("locality", "municipality", "county", "stateProvince", "country"):
        val = resolve_myco_field(occurrence, key)
        if val:
            place_parts.append(val)
    if place_parts:
        params["place_guess"] = ", ".join(place_parts)

    # coordinates
    lat = resolve_myco_field(occurrence, "decimalLatitude")
    lng = resolve_myco_field(occurrence, "decimalLongitude")
    if lat is not None and lng is not None:
        try:
            params["latitude"] = float(lat)
            params["longitude"] = float(lng)
        except (ValueError, TypeError):
            pass
        else:
            unc = resolve_myco_field(occurrence, "coordinateUncertaintyInMeters")
            if unc is not None:
                try:
                    params["positional_accuracy"] = int(float(unc))
                except (ValueError, TypeError):
                    pass

    # description (skeleton)
    params["description"] = "MycoPortal occid: " + str(occid) + "\nSource: " + MYCO_SOURCE_URL + str(occid) + "\n\nThis observation is being imported from MycoPortal. Refresh soon to see the full observation."

    # geoprivacy
    geoprivacy = "open"
    record_security = resolve_myco_field(occurrence, "recordSecurity")
    if record_security is not None:
        try:
            if int(record_security) > 0:
                geoprivacy = "obscured"
        except (ValueError, TypeError):
            pass
    if args.obscure_large_uncertainty and "positional_accuracy" in params:
        try:
            if int(params["positional_accuracy"]) > 100000:
                geoprivacy = "obscured"
        except (ValueError, TypeError):
            pass
    params["geoprivacy"] = geoprivacy

    obj = json.dumps({"observation": params})

    if args.debug:
        print("DEBUG build_myco_creation_obj:")
        print(json.dumps({"observation": params}, indent=2))

    return obj, display_name, verbatim_date_note

def get_myco_fields(occurrence, occid):

    fields = {}

    cat_num = resolve_myco_field(occurrence, "catalogNumber")
    if cat_num:
        fields["9540"] = cat_num

    inst_code = resolve_myco_field(occurrence, "institutionCode")
    coll_code = resolve_myco_field(occurrence, "collectionCode")
    herb_parts = [p for p in (inst_code, coll_code) if p]
    if herb_parts:
        fields["9539"] = " - ".join(herb_parts)

    basis = resolve_myco_field(occurrence, "basisOfRecord")
    if basis and "specimen" in str(basis).lower():
        fields["1162"] = "Yes"

    return fields

def build_myco_notes(occurrence, occid, verbatim_date_note, media_successes, media_failures):

    sections = []

    # 1. Source link
    url = MYCO_SOURCE_URL + str(occid)
    sections.append("Imported from MycoPortal\nSource: <a href=\"" + url + "\">" + url + "</a>")

    # 2. Collector info
    collector_parts = []
    recorded_by = resolve_myco_field(occurrence, "recordedBy")
    if recorded_by:
        collector_parts.append("Collected by: " + recorded_by)
    assoc_collectors = resolve_myco_field(occurrence, "associatedCollectors")
    if assoc_collectors:
        collector_parts.append("Associated collectors: " + assoc_collectors)
    record_number = resolve_myco_field(occurrence, "recordNumber")
    if record_number:
        collector_parts.append("Record number: " + record_number)
    if collector_parts:
        sections.append("\n".join(collector_parts))

    # 3. Determiner
    det_parts = []
    identified_by = resolve_myco_field(occurrence, "identifiedBy")
    date_identified = resolve_myco_field(occurrence, "dateIdentified")
    if not identified_by:
        # try identifications list
        idents = resolve_myco_field(occurrence, "identifications")
        if idents and isinstance(idents, list) and len(idents) > 0:
            identified_by = resolve_myco_field(idents[0], "identifiedBy")
            if not date_identified:
                date_identified = resolve_myco_field(idents[0], "dateIdentified")
    if identified_by:
        det_line = "Determined by: " + identified_by
        if date_identified:
            det_line += " (" + str(date_identified) + ")"
        det_parts.append(det_line)
    if det_parts:
        sections.append("\n".join(det_parts))

    # 4. Habitat/substrate/remarks
    eco_parts = []
    habitat = resolve_myco_field(occurrence, "habitat")
    if habitat:
        eco_parts.append("Habitat: " + habitat)
    substrate = resolve_myco_field(occurrence, "substrate")
    if substrate:
        eco_parts.append("Substrate: " + substrate)
    remarks = resolve_myco_field(occurrence, "occurrenceRemarks")
    if remarks:
        eco_parts.append("Remarks: " + remarks)
    assoc_taxa = resolve_myco_field(occurrence, "associatedTaxa")
    if assoc_taxa:
        eco_parts.append("Associated taxa: " + assoc_taxa)
    if eco_parts:
        sections.append("\n".join(eco_parts))

    # 5. Institution
    inst_parts = []
    inst_code = resolve_myco_field(occurrence, "institutionCode")
    if inst_code:
        inst_parts.append("Institution: " + inst_code)
    coll_code = resolve_myco_field(occurrence, "collectionCode")
    if coll_code:
        inst_parts.append("Collection: " + coll_code)
    cat_num = resolve_myco_field(occurrence, "catalogNumber")
    if cat_num:
        inst_parts.append("Catalog number: " + cat_num)
    if inst_parts:
        sections.append("\n".join(inst_parts))

    # 6. Verbatim date
    if verbatim_date_note:
        sections.append("Verbatim date: " + verbatim_date_note)

    # 7. Elevation
    elevation = resolve_myco_field(occurrence, "minimumElevationInMeters")
    if elevation:
        sections.append("Elevation: " + str(elevation) + " m")

    # 8. Image attribution and source URLs
    if media_successes:
        attr_lines = []
        for i, (img_bytes, media_dict, chosen_url) in enumerate(media_successes):
            attr_parts = []
            if chosen_url:
                attr_parts.append("source: " + chosen_url)
            for attr_key in ("owner", "rights", "copyright", "creator", "accessRights"):
                val = resolve_myco_field(media_dict, attr_key)
                if val:
                    attr_parts.append(attr_key + ": " + val)
            if attr_parts:
                attr_lines.append("Image #" + str(i + 1) + ": " + "; ".join(attr_parts))
        if attr_lines:
            sections.append("\n".join(attr_lines))

    # 9. Failed images
    if media_failures:
        fail_lines = []
        for media_dict, failed_url, reason in media_failures:
            line = str(failed_url) if failed_url else "(no URL)"
            if reason:
                line += " — " + reason
            fail_lines.append(line)
        sections.append("Failed to download images:\n" + "\n".join(fail_lines))

    full_notes = "\n\n".join(sections)

    obj = json.dumps({"observation": {"description": full_notes}, "ignore_photos": "1"})

    return obj

##### MYCOPORTAL ORCHESTRATION

def import_myco_occurrence(occid, args):

    print("\nImporting MycoPortal occurrence " + str(occid) + "...")

    # 1. Fetch occurrence
    occurrence = myco_api.get_occurrence(occid, debug=args.debug)
    if occurrence is None:
        print("Warning: could not fetch occurrence " + str(occid) + ". Skipping.")
        return False

    # 2. Extract media
    media = resolve_myco_field(occurrence, "media", "images", "occurrenceMedia")
    if not isinstance(media, list) or len(media) == 0:
        media = myco_api.get_media_fallback(occid)

    # 3. Dry run
    if args.dry_run:
        sciname = resolve_myco_field(occurrence, "sciname", "scientificName")
        raw_date = resolve_myco_field(occurrence, "eventDate", "date")
        locality = resolve_myco_field(occurrence, "locality")
        lat = resolve_myco_field(occurrence, "decimalLatitude")
        lng = resolve_myco_field(occurrence, "decimalLongitude")
        print("DRY RUN for occid " + str(occid) + ":")
        print("  sciname: " + str(sciname))
        print("  date: " + str(raw_date))
        print("  locality: " + str(locality))
        print("  coords: " + str(lat) + ", " + str(lng))
        print("  media items: " + str(len(media)))
        if media:
            for m in media:
                url = myco_api.pick_media_url(m, args.image_size)
                print("    " + str(url))
        if args.debug:
            print("DEBUG full occurrence:")
            print(json.dumps(occurrence, indent=2))
        return True

    # 4. JWT refresh
    JWT_age = time.time() - iNat_JWT_timestamp
    JWT_time_left = max(INAT_JWT_LIFESPAN - JWT_age, 0)
    if JWT_time_left <= MAX_EXPECTED_MIRROR_TIME:
        set_iNat_info(True)

    # 5. Create skeleton
    creation_obj, display_name, verbatim_date_note = build_myco_creation_obj(occurrence, occid, args)
    print("Creating skeleton observation for \"" + display_name + "\".")

    iNatID = None
    try:
        iNatID = inat_api.create_obs(creation_obj, iNat_JWT)
    except SystemExit:
        # retry once without date — iNat may reject partial/unusual dates
        creation_params = json.loads(creation_obj)
        if "observed_on_string" in creation_params.get("observation", {}):
            print("Create failed. Retrying without date field...")
            if verbatim_date_note is None:
                verbatim_date_note = creation_params["observation"]["observed_on_string"]
            del creation_params["observation"]["observed_on_string"]
            try:
                iNatID = inat_api.create_obs(json.dumps(creation_params), iNat_JWT)
            except SystemExit:
                print("Could not create observation for occid " + str(occid) + ". Skipping.")
                return False
        else:
            print("Could not create observation for occid " + str(occid) + ". Skipping.")
            return False

    if iNatID is None:
        print("Could not create observation for occid " + str(occid) + ". Skipping.")
        return False

    iNatID = str(iNatID)
    print("Created skeleton observation. iNat ID is " + iNatID + ".")

    # 6. Track skeleton
    update_myco_incomplete(occid, iNatID, "skeleton_created")
    if args.debug:
        print("DEBUG stage: skeleton_created")

    # 7. Post fields
    fields = get_myco_fields(occurrence, occid)
    if fields:
        print("Posting observation fields.")
        try:
            inat_api.post_fields(iNatID, fields, iNat_JWT)
            update_myco_incomplete(occid, iNatID, "fields_posted")
            if args.debug:
                print("DEBUG stage: fields_posted")
        except SystemExit:
            print("Warning: failed to post fields for occid " + str(occid) + ". Continuing.")
            update_myco_incomplete(occid, iNatID, "fields_failed")
        except Exception as e:
            print("Warning: exception posting fields for occid " + str(occid) + ": " + repr(e) + ". Continuing.")
            update_myco_incomplete(occid, iNatID, "fields_failed")
    else:
        update_myco_incomplete(occid, iNatID, "fields_posted")

    # 8. Download + upload images
    media_successes = []
    media_failures = []
    upload_failures = []
    if media:
        print("Downloading images from MycoPortal.")
        media_successes, media_failures = myco_api.download_images(
            media,
            preferred_size=args.image_size,
            max_images=args.max_images,
            debug=args.debug
        )
        num_chunk = str(len(media_successes)) + " images" if len(media_successes) != 1 else "1 image"
        print("Got " + num_chunk + ".")
        if media_failures:
            print("Failed to download " + str(len(media_failures)) + " image(s).")

        if media_successes:
            print("Uploading images to iNat.")
            update_myco_incomplete(occid, iNatID, "uploading_photos")
            uploaded = []
            for i, (img_bytes, media_dict, chosen_url) in enumerate(media_successes):
                print("Uploading image #" + str(i + 1) + ".")
                try:
                    inat_api.post_image(iNatID, img_bytes, iNat_JWT)
                    uploaded.append((img_bytes, media_dict, chosen_url))
                except SystemExit:
                    print("Warning: failed to upload image #" + str(i + 1) + " (" + str(chosen_url) + "). Continuing.")
                    upload_failures.append((media_dict, chosen_url, "upload to iNat failed (max retries)"))
                except Exception as e:
                    print("Warning: exception uploading image #" + str(i + 1) + ": " + repr(e) + ". Continuing.")
                    upload_failures.append((media_dict, chosen_url, "upload exception: " + repr(e)))
            media_successes = uploaded
            print("Done uploading images (" + str(len(uploaded)) + " succeeded, " + str(len(upload_failures)) + " failed).")

    all_media_failures = media_failures + upload_failures
    if upload_failures:
        update_myco_incomplete(occid, iNatID, "photos_partial")
    else:
        update_myco_incomplete(occid, iNatID, "photos_uploaded")
    if args.debug:
        print("DEBUG stage: " + ("photos_partial" if upload_failures else "photos_uploaded"))

    # 9. Write full notes
    print("Writing observation notes.")
    note_obj = build_myco_notes(occurrence, occid, verbatim_date_note, media_successes, all_media_failures)
    if args.debug:
        print("DEBUG notes object:")
        print(note_obj)
    try:
        inat_api.update_obs(iNatID, note_obj, iNat_JWT)
        update_myco_incomplete(occid, iNatID, "notes_written")
        if args.debug:
            print("DEBUG stage: notes_written")
    except SystemExit:
        print("Warning: failed to write notes for occid " + str(occid) + ". Observation exists but notes are incomplete.")
        update_myco_incomplete(occid, iNatID, "notes_failed")
        return False
    except Exception as e:
        print("Warning: exception writing notes for occid " + str(occid) + ": " + repr(e))
        update_myco_incomplete(occid, iNatID, "notes_failed")
        return False

    # 10. Complete
    remove_myco_incomplete(occid)
    update_myco_completes(occid)
    print("Done importing occurrence " + str(occid) + " -> iNat " + iNatID + ".")

    return True

def run_myco_import(occids, args):

    completes = load_myco_completes()
    new_occids = [oid for oid in occids if oid not in completes]
    skipped = len(occids) - len(new_occids)

    if skipped > 0:
        print(str(skipped) + " occid(s) already imported (in " + MYCO_COMPLETES_FILENAME + "), skipping.")

    print(str(len(new_occids)) + " of " + str(len(occids)) + " occids are new.")

    if len(new_occids) == 0:
        print("Nothing to import.")
        return

    successes = 0
    failures = 0
    for i, occid in enumerate(new_occids):
        print("\n[" + str(i + 1) + "/" + str(len(new_occids)) + "]")
        result = import_myco_occurrence(occid, args)
        if result:
            successes += 1
        else:
            failures += 1

    print("\nMycoPortal import complete.")
    print("  Successes: " + str(successes))
    print("  Failures: " + str(failures))
    print("  Skipped (already done): " + str(skipped))

def main():

    parser = argparse.ArgumentParser(description="Mirror observations to iNaturalist")
    parser.add_argument("--myco", nargs="*", default=None, help="MycoPortal occids or URLs")
    parser.add_argument("--myco-csv", default=None, help="CSV file with occids")
    parser.add_argument("--max-images", type=int, default=None)
    parser.add_argument("--image-size", choices=["original", "medium", "thumbnail"], default="medium")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--obscure-large-uncertainty", action="store_true")
    args = parser.parse_args()

    if args.myco is not None or args.myco_csv is not None:
        # MycoPortal mode
        LOAD_settings()
        if not args.dry_run:
            set_iNat_info()
            deal_with_myco_incompletes()
        occids = parse_myco_occids(args)
        if len(occids) == 0:
            print("No valid occids provided.")
            return
        run_myco_import(occids, args)
    else:
        # Original MO mode
        print("\nWelcome to the mirroring.")

        LOAD_settings()

        set_MO_info()
        set_iNat_info()

        deal_with_incompletes()

        print("\nGetting IDs of your observations on MO. This will take some time.")
        MOIDs = mo_api.get_all_observations(MO_username)
        print("Got "+str(len(MOIDs))+" IDs.")

        already_mirrored_MOIDs = get_already_mirrored_MOIDs()

        while True:

            ###
            mirrorable_MOIDs = set(MOIDs).difference(already_mirrored_MOIDs)
            ###
            copula = "is" if len(mirrorable_MOIDs) == 1 else "are"
            print("\n"+str(len(mirrorable_MOIDs))+" of your observations "+copula+" mirrorable (not yet on iNat).")
            if len(mirrorable_MOIDs) == 0:
                print("\nNo observations to mirror.")
                input("Press enter to exit.")
                print("Goodbye.\n")
                exit(0)

            num_to_mirror = INPUT_num_to_mirror(len(mirrorable_MOIDs))
            if num_to_mirror == 0:
                print("\nNot mirroring any observations.")
                input("Press enter to exit.")
                print("Goodbye.\n")
                exit(0)

            number_chunk = "1 observation" if num_to_mirror == 1 else str(num_to_mirror)+" observations"
            print("\nMirroring "+number_chunk+".")
            MO_page_to_start = calculate_MO_page_to_start(MOIDs, list(mirrorable_MOIDs))

            ##
            MOIDs_to_mirror = sorted(list(mirrorable_MOIDs))[:num_to_mirror]
            ##
            newly_mirrored_MOIDs = mirror_wrapper(num_to_mirror, list(already_mirrored_MOIDs), MO_page_to_start, MOIDs_to_mirror)

            if len(newly_mirrored_MOIDs) < len(mirrorable_MOIDs):
                print("\nDone mirroring "+number_chunk+". Start again?")

                if INPUT_yes_or_no():
                    already_mirrored_MOIDs = already_mirrored_MOIDs | newly_mirrored_MOIDs
                    continue
                else:
                    print("\nGoodbye.\n")
                    break

            else:
                print("\nDone mirroring all available observations.")
                input("Press enter to exit.")
                print("Goodbye.\n")
                exit(0)
         
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nThe program has crashed.")
        print("Exception: "+repr(e))
        input("\nPress enter to exit.")
        print("Goodbye.\n")
        exit(0)