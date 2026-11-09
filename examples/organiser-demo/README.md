# Example roster and media

These files are inputs for the same event application. Start with `Start-App.ps1`
and follow Event setup. Upload `registrations.csv` through the roster file picker;
then choose who to invite from the monitor and participant lists.

This example contains a two-person team whose members have the
`participant_monitor` role. They require accepted Reviewer membership in the
existing Supervisely team. Connect the admin using their own account and token;
email addresses in a CSV are never login credentials. Populate actual account
logins in the CSV when they differ from emails.

Change the roster, source data and rules to configure another event. There is
no separate application version for these files. See [the application guide](../../README.md)
and [real-account checks](../../docs/real-pilot.md).

## Bundled media and provenance

The dataset includes two CC0 bird photographs, in different `images/`
categories. Its `videos/` branch does not yet contain frame sequences;
test the current folder-or-ZIP importer with a complete video-frame tree.
The photos were resized to 960 pixels wide and JPEG recompressed. Exact
sources and SHA-256 checksums are in
[image-credits.json](image-credits.json).

- `dataset/images/sparrows/bird.jpg`: house sparrow by Mathias Appel, CC0,
  https://commons.wikimedia.org/wiki/File:House_Sparrow(Passer_domesticus).jpg
- `dataset/images/garden/bird.jpg`: house sparrow by Hobbyfotowiki, CC0,
  https://commons.wikimedia.org/wiki/File:House_sparrow_(Passer_domesticus),_Amrum.jpg
