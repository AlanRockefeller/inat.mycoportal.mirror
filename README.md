# Mushroom Observer/iNaturalist Mirror

This is a command-line program that will copy your observations, one-way, from [Mushroom Observer](https://mushroomobserver.org/) to [iNaturalist](https://www.inaturalist.org/). It is designed to require as little input from you as possible, to preserve almost all content from the MO observations, and to fill in iNat Observation Fields where appropriate. The new (iNat) and original (MO) observations will have links to each other; the original observation will not be modified other than by the addition of that link. If and when the original (MO) observation is modified subsequently, this program will NOT reflect the changes on iNat.

It also imports MycoPortal occurrence records, photos, specimen fields, and source attribution into iNaturalist.

## Run from source and import MycoPortal records

With Python 3.9 or later, install the dependency and preview a record:

```sh
python3 -m pip install -r requirements.txt
python3 mirror.py --myco 5649507 --dry-run
```

Import by occurrence ID, full MycoPortal URL, or a CSV with an `occid` column:

```sh
python3 mirror.py --myco 5649507 5649508
python3 mirror.py --myco 'https://mycoportal.org/portal/collections/individual/index.php?occid=5649507'
python3 mirror.py --myco-csv occurrences.csv --image-size original --max-images 3
```

The import prompts for your iNaturalist username and browser authorization. MycoPortal mode does not need a Mushroom Observer account. `--dry-run` fetches and displays source metadata without authenticating to iNaturalist, downloading photos, or writing tracking files. `--debug` displays detailed source data. `--obscure-large-uncertainty` obscures locations with uncertainty over 100 km; records with `recordSecurity` greater than zero are obscured automatically. `--max-images` limits successful image downloads (zero skips photos).

The default medium image size prefers Symbiota's web-ready `url`, and photo attribution includes its `photographer` field. See the [Symbiota media fields](https://docs.symbiota.org/es/3.3/Collection_Manager_Guide/Images/media_upload_url/). Downloads are checked for signatures of JPG, PNG, or GIF, the [image formats documented by iNaturalist](https://help.inaturalist.org/en/support/solutions/articles/151000169928-what-kind-of-photos-or-sounds-should-i-attach-to-observations-), before upload.

Completed occurrence IDs are recorded in `myco_completes.txt`. Failed or interrupted imports remain in `myco_incompletes.tsv` and are skipped on reruns. Field, photo, and note failures keep the import incomplete and produce a nonzero exit status. On the next non-dry run, you can delete tracked partial observations and reimport them.

If creation times out, the program searches for the occurrence marker before proceeding. A missing search result cannot prove that creation failed, so it retains a row with an empty `iNatID` and does not repeat creation. Check your iNaturalist observations before manually reconciling that row: enter the existing observation ID to make it available for the normal incomplete-import cleanup, or remove the row only after confirming that no observation was created. Keep the tracking files between runs and run one importer at a time from a given folder.

Settings, tokens, backups, and runtime tracking files are excluded from Git. They are stored in the current working directory.

## Mushroom Observer recovery

MO imports record creation attempts in `mo_pending.tsv` before contacting iNaturalist. If the response is lost, the importer searches for the MOID marker in the observation description and saves any recovered iNaturalist ID. Pending or unknown MOIDs are excluded from new imports, including after a restart. If search has not found the observation, check iNaturalist before editing the recovery row: set its `iNatID` and `skeleton_created` stage to make the partial observation available for normal cleanup, or remove the row only after confirming that no observation was created.

Once the iNaturalist fields, photos, and notes are written, the record enters `link_pending`. The populated observation is protected from incomplete-observation deletion while its MO back-link is unfinished. On the next run, the importer checks MO notes for the exact iNaturalist observation URL before trying to add it again; an existing link counts as success. Completion is recorded only after that step succeeds. Cleanup of a partial observation also accepts HTTP 404, since an earlier deletion may already have removed it.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests isolate tracking files in temporary directories and block real HTTP requests. GitHub Actions runs the suite on Python 3.9 and 3.14.

## What you will have to do

1. **Download** one of the .rar files in [the latest release](https://github.com/JacobPulk/mirror/releases/latest):  
  either the Windows version (**for Windows 8 or later**), which contains a .exe,    
  or the Python version (**for any OS, as long as you have [Python 3.9](https://www.python.org/downloads/) or later with the [requests module](https://pypi.org/project/requests/) installed**), which contains .py scripts.

2. **Extract** that .rar file to its own folder.

3. In that folder, **run** mirror.exe (Windows version) or mirror.py with Python (Python version).

Hopefully, the rest is self-explanatory.

You will not have to share your MO or iNat password. You **will** be prompted to authorize this program to read/write your observations on both platforms: at the start, you'll be directed to (1) [create an "API Key" on MO](https://mushroomobserver.org/account/api_keys) and supply it; and (2) enter a provided inaturalist.com URL in your browser, where iNat will confirm your login and have your browser send this program the necessary access token.

## What will be preserved on iNaturalist

Observation date

Location name

GPS coordinates (directly if specified; from the center of the rectangle for the named location if unspecified)

GPS uncertainty radius (20m if GPS specified; 1/2 larger of rectangle width or length for named location if unspecified)

GPS privacy ("Obscured" on iNat if and only if "Hidden" on MO)

Observation notes

All images, in order (but BEWARE that iNat does not preserve full-size images)

All image captions (indexed to photo #s and added to Notes - iNat hardly supports photo captions)

Observation title, which will become your "Identification" (ID proposal) on iNat. To get from an MO name to the best equivalent iNat name, the former is first looked up in a human-curated dictionary matching names between platforms, then searched for on iNat for a perfect match (including listed synonyms), then searched for on iNat with just the first word of the name (usually the genus), or finally defaulted to "Life".

All other name proposals, which will be included as the text within that name proposal (including the proposer's MO username and justification, everyone's votes, and the resulting "MO community vote" as a percentage)

MO observation ID/URL

Up to 1 "collection number"

All specimens (herbarium + accession number; up to 1 personal herbarium specimen and 1 other herbarium specimen will be added to Observation Fields; the rest will be added to the Notes)

All sequences (up to 1 each of ITS, LSU, SSU, RPB2, and TEF1 will be added to Observation Fields; the rest will be added to the Notes)

All GenBank accessions for sequences (up to 1 will be added to an Observation Field; the rest will be added to the Notes)

All sequence notes

Date posted to MO

## What (currently) will not be preserved on iNaturalist

### Except for full-resolution images, I imagine most users will deem most of this data inessential for this undertaking. I mirrored my own observations without it.

Full-resolution images (iNat CANNOT do this)

Elevation*

Custom MO observation fields

Image original filenames

Image quality ("No opinion" to "Great")

Image copyright/license information.* Images are posted to iNat under the default CC-BY-NC license.

Image creation, last modified, last viewed timestamps, view count

Collection numbers past the first*

Specimen notes, specimen initial determination

ENA or UNITE accessions for sequences*

Sequence creator, creation, last modified timestamps

Comments*

MyCoPortal links*

Species Lists (some would be best represented as Projects, others as Traditional Projects, Tags, or Observation Fields)

Projects

Observation creation, last modified, last viewed, last viewed by you timestamps, view count*

Observation log

## Special features

By default, all observation information provided by the MO API is saved in a folder with the program, so you can analyze or restore it differently later. This includes some things not currently preserved on iNaturalist, which are marked by \* in the previous section.

A log is kept indicating all of your observation titles that did not get perfect name matches on iNat (useful for you, to manually improve them if you can), as well as names that were not in the included dictionary (useful for this project, to add them for the next release).
