import streamDeck from "@elgato/streamdeck";
import { Dashboard } from "./actions/dashboard";
import { PlayPause } from "./actions/play-pause";
import { Playlist } from "./actions/playlist";
import { Shuffle } from "./actions/shuffle";
import { Skip } from "./actions/skip";
import { Stop } from "./actions/stop";
import { Summon } from "./actions/summon";
import { Voice } from "./actions/voice";
import { VolumeDown } from "./actions/volume-down";
import { VolumeUp } from "./actions/volume-up";
import { initRuntime } from "./runtime";

streamDeck.actions.registerAction(new PlayPause());
streamDeck.actions.registerAction(new Skip());
streamDeck.actions.registerAction(new Shuffle());
streamDeck.actions.registerAction(new Stop());
streamDeck.actions.registerAction(new VolumeUp());
streamDeck.actions.registerAction(new VolumeDown());
streamDeck.actions.registerAction(new Summon());
streamDeck.actions.registerAction(new Playlist());
streamDeck.actions.registerAction(new Dashboard());
streamDeck.actions.registerAction(new Voice());

await streamDeck.connect();
await initRuntime();
