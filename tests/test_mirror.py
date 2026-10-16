import argparse
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests

import both_api
import inat_api
import mirror
import mo_api
import myco_api


def response(status=200, payload=None, content=None):
    result = requests.Response()
    result.status_code = status
    result._content = content if content is not None else json.dumps(payload or {}).encode()
    return result


def arguments(**overrides):
    values = dict(myco=None, myco_csv=None, dry_run=False, debug=False,
                  image_size="medium", max_images=None, obscure_large_uncertainty=False)
    return argparse.Namespace(**(values | overrides))


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch("builtins.print").start()
        # Every test must explicitly mock its HTTP calls. Accidental requests fail.
        patch.object(requests.sessions.Session, "request",
                     side_effect=AssertionError("Unexpected real HTTP request")).start()
        patch.object(both_api, "request_pause").start()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for key, filename in {
            "SETTINGS_FILENAME": "settings.txt", "LOG_FILENAME": "log.txt",
            "COMPLETES_FILENAME": "completes.txt", "INCOMPLETES_FILENAME": "incompletes.txt",
            "MO_PENDING_FILENAME": "mo_pending.tsv",
            "MYCO_COMPLETES_FILENAME": "myco_completes.txt",
            "MYCO_INCOMPLETES_FILENAME": "myco_incompletes.tsv",
        }.items():
            patch.object(mirror, key, str(self.directory / filename)).start()


class RequestTests(IsolatedTest):
    url = "https://api.inaturalist.org/v1/observations"

    def test_creation_accepts_201_without_posting_twice(self):
        post = patch.object(both_api.sess, "post", return_value=response(201, {"id": 7})).start()
        self.assertEqual(inat_api.create_obs("{}", "token"), "7")
        post.assert_called_once()

    def test_post_timeouts_and_bad_responses_are_not_retried(self):
        for outcome in (requests.ReadTimeout(), requests.ConnectTimeout(),
                        requests.ConnectionError(), response(502), response(200, content=b"bad JSON"),
                        response(200, {}), response(204, content=b"")):
            with self.subTest(outcome=outcome):
                with patch.object(both_api.sess, "post") as post:
                    if isinstance(outcome, Exception):
                        post.side_effect = outcome
                    else:
                        post.return_value = outcome
                    with self.assertRaises(both_api.AmbiguousRequestError):
                        inat_api.create_obs("{}", "token")
                    post.assert_called_once()

    def test_get_retries_transient_connection_and_timeout_errors(self):
        with patch.object(both_api.sess, "get", side_effect=[
            requests.ConnectTimeout(), requests.ReadTimeout(), response(503), response(payload={"id": 1})
        ]) as get:
            self.assertEqual(both_api.careful_request("GET", self.url), {"id": 1})
        self.assertEqual(get.call_count, 4)

    def test_retry_budget_raises_regular_exception(self):
        with patch.object(both_api.sess, "get", side_effect=requests.ReadTimeout()) as get:
            with self.assertRaises(both_api.MaxRequestAttemptsExceeded):
                both_api.careful_request("GET", self.url)
        self.assertEqual(get.call_count, both_api.MAX_ATTEMPTS)

    def test_auth_probe_returns_none_but_writes_raise(self):
        with patch.object(both_api.sess, "get", return_value=response(401)):
            self.assertIsNone(both_api.careful_request("GET", self.url))
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method), patch.object(both_api.sess, method, return_value=response(401)) as send:
                with self.assertRaises(both_api.RequestRejected):
                    both_api.careful_request(method.upper(), self.url)
                send.assert_called_once()

    def test_validation_errors_are_not_retried(self):
        with patch.object(both_api.sess, "post", return_value=response(422, {"errors": ["date invalid"]})) as post:
            with self.assertRaises(both_api.RequestRejected) as caught:
                inat_api.create_obs("{}", "token")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("date invalid", caught.exception.content)
        post.assert_called_once()

    def test_rate_limited_post_can_retry_after_explicit_rejection(self):
        with patch.object(both_api.sess, "post", side_effect=[response(429), response(201, {"id": 7})]) as post:
            self.assertEqual(inat_api.create_obs("{}", "token"), "7")
        self.assertEqual(post.call_count, 2)

    def test_204_delete_is_successful(self):
        with patch.object(both_api.sess, "delete", return_value=response(204, content=b"")) as delete:
            self.assertEqual(both_api.careful_request("DELETE", self.url), {})
        delete.assert_called_once()

    def test_json_writes_set_content_type_without_breaking_form_or_photo_uploads(self):
        with patch.object(both_api.sess, "post", return_value=response(payload={"id": 7})) as post:
            inat_api.create_obs("{}", "token")
            self.assertEqual(post.call_args.kwargs["headers"]["Content-Type"], "application/json")
            both_api.careful_request("POST", "https://www.inaturalist.org/oauth/token", data={"code": "code"})
            self.assertIsNone(post.call_args.kwargs["headers"])
            inat_api.post_image("7", b"image", "token")
            self.assertNotIn("Content-Type", post.call_args.kwargs["headers"])

    def test_url_parameters_round_trip(self):
        params = {"notes": "a & b / c? d=é", "year": "1900,1901"}
        url = both_api.build_request_URL("https://example.org", "/api", params)
        self.assertEqual(parse_qs(urlparse(url).query), {k: [v] for k, v in params.items()})
        self.assertEqual(both_api.build_request_URL("https://example.org", "/api"), "https://example.org/api")


class ReviewRegressionTests(IsolatedTest):
    def test_oauth_callback_decodes_values_and_checks_state(self):
        with patch.object(inat_api.socket, "socket") as socket_factory:
            server = socket_factory.return_value.__enter__.return_value
            connection = server.accept.return_value[0]
            server.accept.return_value = (server.accept.return_value[0], (inat_api.LOCAL_HOST, 1))
            connection.recv.return_value = b"GET /?code=a%3Db&state=expected HTTP/1.1\r\n\r\n"
            self.assertEqual(inat_api.get_param_from_socket("code", expected_state="expected"), "a=b")
            connection.settimeout.assert_called()
        with patch.object(inat_api.socket, "socket") as socket_factory:
            server = socket_factory.return_value.__enter__.return_value
            connection = server.accept.return_value[0]
            server.accept.return_value = (server.accept.return_value[0], (inat_api.LOCAL_HOST, 1))
            connection.recv.return_value = b"GET /?code=code&state=wrong HTTP/1.1\r\n\r\n"
            with self.assertRaises(RuntimeError):
                inat_api.get_param_from_socket("code", expected_state="expected")

    def test_pkce_exchange_sends_verifier_in_body(self):
        with patch.object(inat_api, "get_param_from_socket", return_value="code") as callback, patch.object(
            inat_api.secrets, "token_urlsafe", return_value="state"
        ), patch.object(inat_api, "careful_request", side_effect=[{"access_token": "access"}, {"api_token": "jwt"}]) as call:
            self.assertEqual(inat_api.get_JWT_PKCE(), "jwt")
        callback.assert_called_once_with("code", expected_state="state")
        exchange = call.call_args_list[0]
        self.assertEqual(exchange.args[1], "https://www.inaturalist.org/oauth/token")
        self.assertEqual(exchange.kwargs["data"]["code"], "code")
        self.assertEqual(len(exchange.kwargs["data"]["code_verifier"]), 128)

    def test_mirror_year_queries_are_batched(self):
        with patch.object(inat_api, "careful_request", return_value={"results": []}) as call:
            self.assertEqual(inat_api.get_mirrored_MOIDs("tester"), set())
        for request in call.call_args_list:
            years = parse_qs(urlparse(request.args[1]).query)["year"][0].split(",")
            self.assertLessEqual(len(years), 20)

    def test_settings_write_does_not_reload_stale_globals(self):
        Path(mirror.SETTINGS_FILENAME).write_text("iNat JWT\told\niNat JWT timestamp\t1", encoding="utf8")
        with patch.object(mirror, "iNat_JWT", "fresh"), patch.object(mirror, "iNat_JWT_timestamp", 99):
            mirror.update_settings({"last mirrored": "2"})
            self.assertEqual(mirror.iNat_JWT, "fresh")
            self.assertEqual(mirror.iNat_JWT_timestamp, 99)
        self.assertEqual(mirror._read_settings_file()["iNat JWT"], "old")

    def test_saved_jwt_timestamp_is_loaded(self):
        Path(mirror.SETTINGS_FILENAME).write_text("iNat JWT timestamp\t123", encoding="utf8")
        with patch.object(mirror, "iNat_JWT_timestamp", 0):
            mirror.LOAD_settings()
            self.assertEqual(mirror.iNat_JWT_timestamp, 123)

    def test_first_run_tracking_and_log_files(self):
        mirror.LOAD_log()
        mirror.update_completes(["12"], True)
        mirror.update_incompletes("98", True)
        self.assertEqual(mirror.LOAD_mirroreds(), ["12"])
        self.assertEqual(mirror.LOAD_incompletes(), ["98"])
        mirror.update_incompletes("98", False)
        self.assertEqual(mirror.LOAD_incompletes(), [])

    def test_bad_tags_do_not_trigger_write(self):
        for payload in (None, {}, {"results": []}, {"results": [None]}, {"results": [{"tags": "invalid"}]}):
            with self.subTest(payload=payload), patch.object(inat_api, "careful_request", return_value=payload) as call:
                inat_api.post_tag("1", "new", "token")
                call.assert_called_once()

    def test_null_tags_can_be_added_and_existing_tags_are_preserved(self):
        for tags in (None, ["existing"]):
            with self.subTest(tags=tags), patch.object(inat_api, "careful_request", side_effect=[
                {"results": [{"tags": tags}]}, {}
            ]) as call:
                inat_api.post_tag("1", "new", "token")
                data = json.loads(call.call_args.kwargs["data"])
                self.assertEqual(data["observation"]["tag_list"], "existing, new" if tags else "new")

    def test_missing_proposal_raises_catchable_exception(self):
        for payload in (None, {"results": []}, {"results": [{"identifications": [{}]}]}):
            with self.subTest(payload=payload), patch.object(inat_api, "careful_request", return_value=payload):
                with self.assertRaises(RuntimeError):
                    inat_api.get_existing_proposal("1")

    def test_mo_notes_go_in_patch_body(self):
        with patch.object(mo_api, "careful_request", side_effect=[{"results": [{"notes": "old"}]}, {}]) as call:
            mo_api.add_link("user", "1", "2", "today", "secret")
        args, kwargs = call.call_args
        self.assertEqual(args, ("PATCH", "https://mushroomobserver.org/api2/observations"))
        self.assertIn("old", kwargs["data"]["set_notes"])
        self.assertEqual(kwargs["data"]["api_key"], "secret")

    def test_mo_failed_image_keeps_remaining_caption_aligned(self):
        obs = {"id": 1, "images": [
            {"notes": "first", "original_url": "https://example.org/first.RAW"},
            {"notes": "second", "original_url": "https://example.org/second.jpg"},
        ]}
        with patch.object(mo_api, "careful_request", side_effect=[None, b"x" * 6000]) as call:
            images, captions = mirror.get_image_data(obs)
        self.assertEqual(images, [b"x" * 6000])
        self.assertEqual(captions, ["second"])
        self.assertEqual(call.call_args_list[0].args[1], "https://example.org/first.jpg")

    def test_mo_error_dump_uses_response_and_text_serialization(self):
        # Redirect the diagnostic file without changing the process working directory.
        real_open = open
        def diagnostic_open(name, *args, **kwargs):
            return real_open(self.directory / name, *args, **kwargs)
        with patch.object(mo_api, "careful_request", return_value={"error": "bad"}), patch("builtins.open", side_effect=diagnostic_open):
            with self.assertRaises(SystemExit) as caught:
                mo_api.get_single_obs("user", "1", "key")
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(json.loads((self.directory / "JSON_dump.json").read_text()), {"error": "bad"})

    def test_bad_api_key_always_returns_boolean(self):
        for payload in (None, {}, {"errors": []}, {"errors": [{"code": "BadApiKey", "details": "Bad key"}]}):
            self.assertIsInstance(mo_api.bad_API_key(payload), bool)

    def test_species_list_error_returns_iterable(self):
        with patch.object(mo_api, "careful_request", return_value={}):
            self.assertEqual(mo_api.read_splist("1"), [])

    def test_empty_observations_are_handled(self):
        for module in (inat_api, mo_api):
            for payload in (None, {}, {"results": []}):
                with self.subTest(module=module.__name__, payload=payload), patch.object(module, "careful_request", return_value=payload):
                    self.assertIsNone(module.view_particular("1"))

    def test_radius_uses_latitude_in_radians(self):
        self.assertAlmostEqual(mirror.calculate_radius(60, 60, 1, 0), 40042000 / 360 / 4)


class MycoImportTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        patch.object(mirror, "iNat_JWT_timestamp", time.time()).start()
        patch.object(mirror, "iNat_username", "tester").start()
        self.occurrence = {"catalogNumber": "ABC", "eventDate": "2020-01-02"}
        self.fetch = patch.object(myco_api, "get_occurrence", return_value=self.occurrence).start()
        patch.object(myco_api, "get_media_fallback", return_value=[]).start()
        self.create = patch.object(inat_api, "create_obs", return_value="99").start()
        self.fields = patch.object(inat_api, "post_fields").start()
        self.upload = patch.object(inat_api, "post_image").start()
        self.notes = patch.object(inat_api, "update_obs").start()
        self.search = patch.object(inat_api, "find_obs_by_description", return_value=None).start()

    def test_success_tracks_completion_and_keeps_source_marker(self):
        self.assertTrue(mirror.import_myco_occurrence("123", arguments()))
        self.assertEqual(mirror.load_myco_completes(), {"123"})
        self.assertEqual(mirror.load_myco_incompletes(), [])
        notes = json.loads(self.notes.call_args.args[1])["observation"]["description"]
        self.assertIn("MycoPortal occid: 123", notes)

    def test_unknown_create_is_not_retried_or_marked_complete(self):
        self.create.side_effect = both_api.AmbiguousRequestError("timeout")
        self.assertFalse(mirror.import_myco_occurrence("123", arguments()))
        self.create.assert_called_once()
        self.assertEqual(mirror.load_myco_completes(), set())
        self.assertEqual(mirror.load_myco_incompletes(), [{"occid": "123", "iNatID": "", "stage": "creation_unknown"}])

    def test_search_failure_does_not_permit_reposting(self):
        self.create.side_effect = both_api.AmbiguousRequestError("timeout")
        self.search.side_effect = RuntimeError("search failed")
        self.assertFalse(mirror.import_myco_occurrence("123", arguments()))
        self.create.assert_called_once()
        self.assertEqual(mirror.load_myco_incompletes()[0]["stage"], "creation_unknown")

    def test_unknown_create_can_adopt_existing_observation(self):
        self.create.side_effect = both_api.AmbiguousRequestError("timeout")
        self.search.return_value = "88"
        self.assertTrue(mirror.import_myco_occurrence("123", arguments()))
        self.create.assert_called_once()
        self.assertEqual(self.fields.call_args.args[0], "88")

    def test_only_explicit_date_rejection_allows_retry(self):
        self.create.side_effect = [both_api.RequestRejected(422, "observed_on is invalid"), "99"]
        self.assertTrue(mirror.import_myco_occurrence("123", arguments()))
        self.assertEqual(self.create.call_count, 2)
        second_payload = json.loads(self.create.call_args.args[0])
        self.assertNotIn("observed_on_string", second_payload["observation"])
        self.assertIn("Verbatim date: 2020-01-02", self.notes.call_args.args[1])

    def test_non_date_rejection_is_not_retried(self):
        self.create.side_effect = both_api.RequestRejected(422, "taxon is invalid")
        self.assertFalse(mirror.import_myco_occurrence("123", arguments()))
        self.create.assert_called_once()
        self.assertEqual(mirror.load_myco_incompletes(), [])

    def test_interrupt_after_creation_attempt_has_tracking(self):
        self.create.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            mirror.import_myco_occurrence("123", arguments())
        self.assertEqual(mirror.load_myco_incompletes()[0]["stage"], "creation_pending")

    def test_fields_or_notes_failures_remain_incomplete(self):
        for failing_mock, expected_stage in ((self.fields, "fields_failed"), (self.notes, "notes_failed")):
            with self.subTest(stage=expected_stage):
                failing_mock.side_effect = both_api.RequestRejected(401)
                self.assertFalse(mirror.import_myco_occurrence("123", arguments()))
                self.assertEqual(mirror.load_myco_incompletes()[0]["stage"], expected_stage)
                self.assertEqual(mirror.load_myco_completes(), set())
                failing_mock.side_effect = None

    def test_photo_failure_remains_incomplete_and_is_reported_in_notes(self):
        self.occurrence["media"] = [{"url": "https://example.org/photo.jpg"}]
        image = (b"image", self.occurrence["media"][0], "https://example.org/photo.jpg")
        self.upload.side_effect = both_api.RequestRejected(401)
        with patch.object(myco_api, "download_images", return_value=([image], [])):
            self.assertFalse(mirror.import_myco_occurrence("123", arguments()))
        self.assertEqual(mirror.load_myco_incompletes()[0]["stage"], "photos_partial")
        self.assertEqual(mirror.load_myco_completes(), set())
        self.assertIn("upload to iNat failed", self.notes.call_args.args[1])

    def test_download_failure_remains_incomplete(self):
        self.occurrence["media"] = [{"url": "https://example.org/photo.jpg"}]
        with patch.object(myco_api, "download_images", return_value=([], [({}, "url", "failed")])):
            self.assertFalse(mirror.import_myco_occurrence("123", arguments()))
        self.assertEqual(mirror.load_myco_incompletes()[0]["stage"], "photos_partial")

    def test_retained_incomplete_is_not_imported_again(self):
        mirror.update_myco_incomplete("123", "99", "notes_failed")
        self.assertFalse(mirror.run_myco_import(["123"], arguments()))
        self.create.assert_not_called()

    def test_completed_import_is_skipped_successfully(self):
        mirror.update_myco_completes("123")
        self.assertTrue(mirror.run_myco_import(["123"], arguments()))
        self.create.assert_not_called()

    def test_cleanup_does_not_delete_completed_or_unknown_observations(self):
        mirror.update_myco_completes("123")
        mirror.update_myco_incomplete("123", "99", "notes_written")
        mirror.update_myco_incomplete("124", "", "creation_unknown")
        with patch.object(inat_api, "delete_observation") as delete, patch.object(mirror, "INPUT_yes_or_no") as ask:
            mirror.deal_with_myco_incompletes()
        delete.assert_not_called()
        ask.assert_not_called()
        self.assertEqual(mirror.load_myco_incompletes(), [{"occid": "124", "iNatID": "", "stage": "creation_unknown"}])

    def test_cleanup_retains_failed_delete_and_preserves_unknown_rows(self):
        mirror.update_myco_incomplete("1", "91", "notes_failed")
        mirror.update_myco_incomplete("2", "92", "notes_failed")
        mirror.update_myco_incomplete("3", "", "creation_unknown")
        with patch.object(mirror, "INPUT_yes_or_no", return_value=True), patch.object(
            inat_api, "delete_observation", side_effect=[None, both_api.RequestRejected(401)]
        ):
            with self.assertRaises(both_api.RequestRejected):
                mirror.deal_with_myco_incompletes()
        self.assertEqual({r["occid"] for r in mirror.load_myco_incompletes()}, {"2", "3"})

    def test_tracking_survives_failed_atomic_replacement(self):
        mirror.update_myco_incomplete("1", "91", "notes_failed")
        with patch.object(mirror.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                mirror.update_myco_incomplete("2", "92", "notes_failed")
        self.assertEqual([r["occid"] for r in mirror.load_myco_incompletes()], ["1"])

    def test_dry_run_does_not_authenticate_or_create_files(self):
        with patch.object(mirror, "set_iNat_info") as authenticate, patch("sys.argv", ["mirror.py", "--myco", "123", "--dry-run"]):
            self.assertIsNone(mirror.main())
        self.create.assert_not_called()
        authenticate.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_batch_continues_after_one_occurrence_fails(self):
        with patch.object(mirror, "import_myco_occurrence", side_effect=[ValueError("bad data"), True]) as run:
            self.assertFalse(mirror.run_myco_import(["1", "2"], arguments()))
        self.assertEqual(run.call_count, 2)


class MappingTests(IsolatedTest):
    def test_source_marker_does_not_match_another_occid_prefix(self):
        with patch.object(inat_api, "careful_request", return_value={"results": [
            {"id": 1, "description": "MycoPortal occid: 1234"},
            {"id": 2, "description": None},
            {"id": 3, "description": "MycoPortal occid: 123\nSource: ..."},
        ]}):
            self.assertEqual(inat_api.find_obs_by_description("tester", "MycoPortal occid: 123"), "3")

    def test_search_checks_later_pages(self):
        page = [{"id": i, "description": "unrelated"} for i in range(200)]
        with patch.object(inat_api, "careful_request", side_effect=[
            {"results": page}, {"results": [{"id": 999, "description": "MycoPortal occid: 123"}]}
        ]) as call:
            self.assertEqual(inat_api.find_obs_by_description("tester", "MycoPortal occid: 123"), "999")
        self.assertEqual(parse_qs(urlparse(call.call_args.args[1]).query)["page"], ["2"])

    def test_max_images_counts_successes(self):
        media = [{"url": "bad"}, {"url": "good"}, {"url": "extra"}]
        with patch.object(myco_api, "download_image", side_effect=[(None, "failed"), (b"image", None)]) as download:
            successes, failures = myco_api.download_images(media, max_images=1)
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(download.call_count, 2)

    def test_image_download_uses_shared_retry_layer(self):
        image_bytes = b"\xff\xd8\xff" + b"x" * 5997
        with patch.object(both_api.sess, "get", side_effect=[requests.ReadTimeout(), response(content=image_bytes)]) as get:
            image, error = myco_api.download_image("https://example.org/photo.jpg", timeout_seconds=10)
        self.assertEqual(image, image_bytes)
        self.assertIsNone(error)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["timeout"], 10)

    def test_invalid_dates_are_preserved_verbatim(self):
        for value in ("2020-02-30", "2020-19-00", "2020-00-01", "2020-19", "spring 2020"):
            self.assertEqual(mirror.normalize_myco_date(value), (None, value))
        self.assertEqual(mirror.normalize_myco_date("2020-02-00"), ("2020-02", "2020-02-00"))
        self.assertEqual(mirror.normalize_myco_date("2020-00-00"), ("2020", "2020-00-00"))

    def test_invalid_coordinates_are_not_partially_posted(self):
        for latitude, longitude in (("12", "bad"), ("NaN", "12"), ("12", "Infinity"), ("91", "0"), ("12", "181")):
            with self.subTest(latitude=latitude, longitude=longitude):
                obj, _, _ = mirror.build_myco_creation_obj({"decimalLatitude": latitude, "decimalLongitude": longitude}, "1", arguments())
                self.assertNotIn("latitude", json.loads(obj)["observation"])
                self.assertNotIn("longitude", json.loads(obj)["observation"])

    def test_zero_coordinates_and_invalid_uncertainty(self):
        obj, _, _ = mirror.build_myco_creation_obj({
            "decimalLatitude": "0", "decimalLongitude": "12", "coordinateUncertaintyInMeters": "Infinity"
        }, "1", arguments())
        params = json.loads(obj)["observation"]
        self.assertEqual(params["latitude"], 0)
        self.assertNotIn("positional_accuracy", params)

    def test_headerless_csv_keeps_first_url_and_ignores_missing_cells(self):
        csv = self.directory / "occids.csv"
        csv.write_text("https://mycoportal.org/?occid=123\n456\n", encoding="utf8")
        self.assertEqual(mirror.parse_myco_occids(arguments(myco_csv=str(csv))), ["123", "456"])
        csv.write_text("name,occid\nmissing\npresent,123\n", encoding="utf8")
        self.assertEqual(mirror.parse_myco_occids(arguments(myco_csv=str(csv))), ["123"])


if __name__ == "__main__":
    unittest.main()
