import { Config } from '@remotion/cli/config';

// H.264 in an MP4 plays everywhere the demo needs to go: GitHub READMEs,
// Product Hunt, X, LinkedIn. CRF 18 keeps text crisp at 1080p without a
// file size that makes the README slow to load.
Config.setVideoImageFormat('jpeg');
Config.setCodec('h264');
Config.setCrf(18);
Config.setOverwriteOutput(true);
