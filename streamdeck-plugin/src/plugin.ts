import streamDeck from "@elgato/streamdeck";
import { NowPlaying } from "./actions/now-playing";
import { PlayPause } from "./actions/play-pause";
import { Skip } from "./actions/skip";
import { Stop } from "./actions/stop";
import { VolumeDown } from "./actions/volume-down";
import { VolumeUp } from "./actions/volume-up";
import { initRuntime } from "./runtime";

streamDeck.actions.registerAction(new PlayPause());
streamDeck.actions.registerAction(new Skip());
streamDeck.actions.registerAction(new Stop());
streamDeck.actions.registerAction(new VolumeUp());
streamDeck.actions.registerAction(new VolumeDown());
streamDeck.actions.registerAction(new NowPlaying());

await streamDeck.connect();
await initRuntime();
