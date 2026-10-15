import json
import time
from unittest.mock import patch

import requests

import both_api
import inat_api
import mirror
import mo_api
import myco_api
from test_mirror import IsolatedTest, response


class MORecoveryTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        patch.object(mirror, "keep_backup", False).start()
        patch.object(mirror, "iNat_JWT_timestamp", time.time()).start()
        patch.object(mirror, "iNat_username", "tester").start()
        patch.object(mirror, "MO_username", "tester").start()
        patch.object(mirror, "LOAD_taxon_dictionary").start()
        patch.object(mirror, "INPUT_allow_quit").start()
        self.pages = patch.object(mo_api, "get_full_obses", side_effect=[[{"id": 12}], []]).start()
        obj = json.dumps({"observation": {"description": mirror.MO_SOURCE_MARKER + "12"}})
        patch.object(mirror, "build_creation_obj", return_value=(obj, "Fungus", None)).start()
        self.create = patch.object(inat_api, "create_obs", return_value="99").start()
        self.search = patch.object(inat_api, "find_obs_by_description", return_value=None).start()
        patch.object(mirror, "get_fields", return_value=({}, [], [], [])).start()
        self.fields = patch.object(inat_api, "post_fields").start()
        patch.object(inat_api, "get_existing_proposal", return_value=("1", "2", "Fungus", "genus")).start()
        patch.object(mirror, "build_proposal_obj", return_value="{}").start()
        patch.object(inat_api, "update_proposal").start()
        patch.object(mirror, "get_image_data", return_value=([], [])).start()
        self.photos = patch.object(mirror, "post_images").start()
        patch.object(mirror, "build_note_obj", return_value="{}").start()
        self.notes = patch.object(inat_api, "update_obs").start()
        self.link = patch.object(mo_api, "add_link").start()

    def run_mirror(self):
        return mirror.mirror_wrapper(1, [], 1, ["12"])

    def test_pending_state_exists_before_creation_request(self):
        def create(*args):
            self.assertEqual(mirror.load_mo_pending(), [{"MOID": "12", "iNatID": "", "stage": "creation_pending"}])
            return "99"
        self.create.side_effect = create
        self.assertEqual(self.run_mirror(), {"12"})
        self.assertEqual(mirror.LOAD_mirroreds(), ["12"])
        self.assertEqual(mirror.LOAD_incompletes(), [])
        self.assertEqual(mirror.load_mo_pending(), [])

    def test_unknown_creation_is_retained_and_not_repeated_on_rerun(self):
        self.create.side_effect = both_api.AmbiguousRequestError("timeout")
        self.assertEqual(self.run_mirror(), set())
        self.assertEqual(mirror.load_mo_pending(), [{"MOID": "12", "iNatID": "", "stage": "creation_unknown"}])
        self.pages.side_effect = [[{"id": 12}], []]
        self.assertEqual(self.run_mirror(), set())
        self.create.assert_called_once()
        self.fields.assert_not_called()

    def test_interrupt_during_creation_keeps_pending_state(self):
        self.create.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_mirror()
        self.assertEqual(mirror.load_mo_pending()[0]["stage"], "creation_pending")
        self.assertEqual(mirror.LOAD_incompletes(), [])

    def test_explicit_rejection_does_not_leave_unknown_creation(self):
        self.create.side_effect = both_api.RequestRejected(401)
        with self.assertRaises(both_api.RequestRejected):
            self.run_mirror()
        self.assertEqual(mirror.load_mo_pending(), [])

    def test_recovered_creation_id_is_saved_before_recovery_continues(self):
        self.create.side_effect = both_api.AmbiguousRequestError("timeout")
        self.search.return_value = "88"
        update = mirror.update_incompletes
        def check_tracking(iNatID, adding):
            if adding:
                self.assertEqual(mirror.load_mo_pending(), [{"MOID": "12", "iNatID": "88", "stage": "skeleton_created"}])
            return update(iNatID, adding)
        with patch.object(mirror, "update_incompletes", side_effect=check_tracking):
            self.assertEqual(self.run_mirror(), {"12"})
        self.create.assert_called_once()
        self.assertEqual(self.fields.call_args.args[0], "88")

    def test_search_failure_keeps_unknown_creation_blocked(self):
        self.create.side_effect = both_api.AmbiguousRequestError("timeout")
        self.search.side_effect = RuntimeError("search unavailable")
        self.assertEqual(self.run_mirror(), set())
        self.assertEqual(mirror.load_mo_pending()[0]["stage"], "creation_unknown")
        self.create.assert_called_once()

    def test_backlink_failure_retains_populated_observation_outside_cleanup(self):
        self.link.side_effect = both_api.RequestRejected(403)
        self.assertEqual(self.run_mirror(), set())
        self.notes.assert_called_once()
        self.assertEqual(mirror.LOAD_incompletes(), [])
        self.assertEqual(mirror.LOAD_mirroreds(), [])
        self.assertEqual(mirror.load_mo_pending(), [{"MOID": "12", "iNatID": "99", "stage": "link_pending"}])
        # Simulate an interrupt between persisting link_pending and removing the old row.
        mirror.update_incompletes("99", True)
        with patch.object(inat_api, "delete_observation") as delete, patch.object(mirror, "INPUT_yes_or_no") as ask:
            mirror.deal_with_incompletes()
        delete.assert_not_called()
        ask.assert_not_called()
        self.assertEqual(mirror.load_mo_pending()[0]["stage"], "link_pending")

    def test_uncertain_backlink_recovery_does_not_repeat_inat_writes(self):
        self.link.side_effect = both_api.AmbiguousRequestError("lost response")
        self.assertEqual(self.run_mirror(), set())
        self.link.side_effect = None
        with patch.object(inat_api, "delete_observation") as delete:
            mirror.deal_with_incompletes()
        self.assertEqual(mirror.LOAD_mirroreds(), ["12"])
        self.assertEqual(mirror.LOAD_incompletes(), [])
        self.assertEqual(mirror.load_mo_pending(), [])
        delete.assert_not_called()
        self.create.assert_called_once()
        self.fields.assert_called_once()
        self.photos.assert_called_once()
        self.notes.assert_called_once()

    def test_completed_record_survives_failure_to_remove_pending_row(self):
        with patch.object(mirror, "remove_mo_pending", side_effect=OSError("disk error")):
            self.assertEqual(self.run_mirror(), set())
        self.assertEqual(mirror.LOAD_mirroreds(), ["12"])
        self.link.reset_mock()
        with patch.object(inat_api, "delete_observation") as delete, patch.object(mirror, "INPUT_yes_or_no") as ask:
            mirror.deal_with_incompletes()
        self.assertEqual(mirror.load_mo_pending(), [])
        delete.assert_not_called()
        ask.assert_not_called()
        self.link.assert_not_called()

    def test_startup_reconciles_missing_id_before_partial_cleanup(self):
        mirror.update_mo_pending("12", "", "creation_unknown")
        self.search.return_value = "88"
        with patch.object(mirror, "INPUT_yes_or_no", return_value=False), patch.object(inat_api, "delete_observation") as delete:
            mirror.deal_with_incompletes()
        self.assertEqual(mirror.load_mo_pending(), [{"MOID": "12", "iNatID": "88", "stage": "skeleton_created"}])
        self.assertEqual(mirror.LOAD_incompletes(), ["88"])
        self.create.assert_not_called()
        delete.assert_not_called()

    def test_unknown_creation_is_not_offered_for_deletion(self):
        mirror.update_mo_pending("12", "", "creation_unknown")
        with patch.object(mirror, "INPUT_yes_or_no") as ask, patch.object(inat_api, "delete_observation") as delete:
            mirror.deal_with_incompletes()
        ask.assert_not_called()
        delete.assert_not_called()
        self.assertEqual(len(mirror.load_mo_pending()), 1)

    def test_cleanup_removes_both_partial_tracking_records(self):
        mirror.update_mo_pending("12", "99", "skeleton_created")
        mirror.update_incompletes("99", True)
        with patch.object(mirror, "INPUT_yes_or_no", return_value=True), patch.object(inat_api, "delete_observation") as delete:
            mirror.deal_with_incompletes()
        delete.assert_called_once_with("99", mirror.iNat_JWT)
        self.assertEqual(mirror.LOAD_incompletes(), [])
        self.assertEqual(mirror.load_mo_pending(), [])

    def test_failed_cleanup_preserves_both_partial_tracking_records(self):
        mirror.update_mo_pending("12", "99", "skeleton_created")
        with patch.object(mirror, "INPUT_yes_or_no", return_value=True), patch.object(
            inat_api, "delete_observation", side_effect=both_api.RequestRejected(401)
        ):
            with self.assertRaises(both_api.RequestRejected):
                mirror.deal_with_incompletes()
        self.assertEqual(mirror.LOAD_incompletes(), ["99"])
        self.assertEqual(mirror.load_mo_pending()[0]["iNatID"], "99")

    def test_remote_field_lookup_does_not_mark_link_pending_complete(self):
        mirror.update_mo_pending("12", "99", "link_pending")
        with patch.object(mirror, "mirroreds_pause"), patch.object(inat_api, "get_mirrored_MOIDs", return_value={"12", "13"}):
            self.assertEqual(mirror.get_already_mirrored_MOIDs(), {"13"})
        self.assertEqual(mirror.LOAD_mirroreds(), ["13"])

    def test_no_new_import_is_offered_when_only_pending_moids_remain(self):
        mirror.update_mo_pending("12", "", "creation_unknown")
        with patch("sys.argv", ["mirror.py"]), patch.object(mirror, "set_MO_info"), patch.object(
            mirror, "set_iNat_info"
        ), patch.object(mirror, "deal_with_incompletes"), patch.object(
            mo_api, "get_all_observations", return_value=["12"]
        ), patch.object(mirror, "get_already_mirrored_MOIDs", return_value=set()), patch.object(
            mirror, "INPUT_num_to_mirror"
        ) as ask:
            self.assertEqual(mirror.main(), 1)
        ask.assert_not_called()
        self.create.assert_not_called()


class MediaAndCleanupTests(IsolatedTest):
    def test_symbiota_size_preferences(self):
        media = {"url": "web.jpg", "originalUrl": "archive.tif", "mediumUrl": "medium.jpg"}
        self.assertEqual(myco_api.pick_media_url(media, "medium"), "web.jpg")
        self.assertEqual(myco_api.pick_media_url(media, "thumbnail"), "web.jpg")
        self.assertEqual(myco_api.pick_media_url(media, "original"), "archive.tif")
        media["thumbnailUrl"] = "thumb.jpg"
        self.assertEqual(myco_api.pick_media_url(media, "thumbnail"), "thumb.jpg")
        self.assertEqual(myco_api.pick_media_url({"accessUri": "fallback.jpg"}, "medium"), "fallback.jpg")

    def test_documented_image_signatures_are_accepted(self):
        for signature in (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a"):
            content = signature + b"x" * 6000
            with self.subTest(signature=signature), patch.object(myco_api, "careful_request", return_value=content):
                self.assertEqual(myco_api.download_image("https://example.org/image"), (content, None))

    def test_large_html_and_unsupported_formats_are_rejected(self):
        for content in (b"<html>" + b"x" * 6000, b"II*\x00" + b"x" * 6000, b"RIFF" + b"x" * 6000):
            with self.subTest(prefix=content[:6]), patch.object(myco_api, "careful_request", return_value=content):
                image, error = myco_api.download_image("https://example.org/image")
            self.assertIsNone(image)
            self.assertIn("not a supported", error)

    def test_photographer_and_other_attribution_are_preserved(self):
        media = {"photographer": "Example Photographer", "creator": "Creator", "rights": "CC BY"}
        obj = mirror.build_myco_notes({}, "12", None, [(b"image", media, "https://example.org/photo.jpg")], [])
        notes = json.loads(obj)["observation"]["description"]
        self.assertIn("photographer: Example Photographer", notes)
        self.assertIn("creator: Creator", notes)
        self.assertIn("rights: CC BY", notes)

    def test_delete_retry_accepts_already_absent_myco_observation(self):
        mirror.update_myco_incomplete("12", "99", "notes_failed")
        with patch.object(mirror, "INPUT_yes_or_no", return_value=True), patch.object(
            both_api.sess, "delete", side_effect=[requests.ReadTimeout(), response(404)]
        ) as delete:
            mirror.deal_with_myco_incompletes()
        self.assertEqual(delete.call_count, 2)
        self.assertEqual(mirror.load_myco_incompletes(), [])

    def test_already_absent_mo_partial_can_be_cleaned_up(self):
        mirror.update_mo_pending("12", "99", "skeleton_created")
        with patch.object(mirror, "INPUT_yes_or_no", return_value=True), patch.object(
            both_api.sess, "delete", return_value=response(404)
        ):
            mirror.deal_with_incompletes()
        self.assertEqual(mirror.LOAD_incompletes(), [])
        self.assertEqual(mirror.load_mo_pending(), [])

    def test_deletion_authorization_errors_are_not_treated_as_absence(self):
        with patch.object(both_api.sess, "delete", return_value=response(403)):
            with self.assertRaises(both_api.RequestRejected):
                inat_api.delete_observation("99", "token")

    def test_existing_backlink_is_success_without_patch(self):
        for url in ("http://inaturalist.org/observations/99", "https://www.inaturalist.org/observations/99?x=1"):
            with self.subTest(url=url), patch.object(mo_api, "careful_request", return_value={"results": [{"notes": '<a href="' + url + '">existing</a>'}]}) as call:
                mo_api.add_link("tester", "12", "99", "today", "key")
                call.assert_called_once()

    def test_backlink_check_does_not_match_a_different_observation_id(self):
        with patch.object(mo_api, "careful_request", side_effect=[
            {"results": [{"notes": "https://www.inaturalist.org/observations/999"}]}, {}
        ]) as call:
            mo_api.add_link("tester", "12", "99", "today", "key")
        self.assertEqual(call.call_args.args[0], "PATCH")
        self.assertFalse(call.call_args.kwargs["retry_safe"])

    def test_bad_notes_and_patch_errors_raise_catchable_errors(self):
        for payload in (None, {}, {"results": []}):
            with self.subTest(payload=payload), patch.object(mo_api, "careful_request", return_value=payload):
                with self.assertRaises(RuntimeError):
                    mo_api.add_link("tester", "12", "99", "today", "key")
        with patch.object(mo_api, "careful_request", side_effect=[
            {"results": [{"notes": "old"}]}, {"errors": [{"code": "Denied"}]}
        ]):
            with self.assertRaises(RuntimeError):
                mo_api.add_link("tester", "12", "99", "today", "key")

    def test_mo_source_marker_survives_notes_update(self):
        obs = {"id": 12, "consensus": {"name": "Fungus"}, "date": "2020-01-02",
               "location": {"name": "Example"}, "latitude": 1, "longitude": 1,
               "created_at": "2020-01-02T00:00:00.000Z"}
        with patch.object(mirror, "determine_taxon_ID", return_value="1"):
            creation, _, _ = mirror.build_creation_obj(obs)
        notes = mirror.build_note_obj(obs, [], [], [], [])
        for obj in (creation, notes):
            self.assertIn(mirror.MO_SOURCE_MARKER + "12", json.loads(obj)["observation"]["description"])
