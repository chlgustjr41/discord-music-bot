import { useNavigate } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  ArrowLeft,
  Bot,
  ExternalLink,
  Music,
  Shield,
  Terminal,
  Globe,
  Lock,
  Zap,
  Eye,
  MessageSquare,
  Link2,
  BookOpen,
  Headphones,
  Volume2,
  Hash,
} from "lucide-react";

const BOT_CLIENT_ID = import.meta.env.VITE_DISCORD_CLIENT_ID || "";

export function SetupGuide() {
  const navigate = useNavigate();

  const botInviteUrl = BOT_CLIENT_ID
    ? `https://discord.com/oauth2/authorize?client_id=${BOT_CLIENT_ID}&permissions=3230720&scope=bot`
    : "";

  return (
    <div className="mx-auto max-w-3xl p-4 pb-16 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 pt-2">
        <Button variant="ghost" size="sm" onClick={() => navigate("/")}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back
        </Button>
      </div>

      <div className="text-center space-y-2">
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
          <Music className="h-8 w-8 text-primary" />
        </div>
        <h1 className="text-3xl font-bold">Jacky Music Setup Guide</h1>
        <p className="text-muted-foreground max-w-lg mx-auto">
          A Discord music bot with a real-time web dashboard. Play music in your
          server and control it from your browser.
        </p>
      </div>

      <Separator />

      {/* ───────── Features Overview ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Zap className="h-5 w-5 text-primary" />
          Features
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {[
            {
              icon: <Headphones className="h-4 w-4" />,
              title: "Music Playback",
              desc: "Play from YouTube, SoundCloud, and more via Discord commands or the web dashboard.",
            },
            {
              icon: <Globe className="h-4 w-4" />,
              title: "Web Dashboard",
              desc: "Real-time controls, queue management, search, and playlists — all from your browser.",
            },
            {
              icon: <Volume2 className="h-4 w-4" />,
              title: "Local Audio Node",
              desc: "Run the audio engine on your own machine for lower latency. No port forwarding needed.",
            },
            {
              icon: <BookOpen className="h-4 w-4" />,
              title: "Playlists & History",
              desc: "Save playlists, view listening history, and track command activity per server.",
            },
          ].map((f) => (
            <Card key={f.title}>
              <CardContent className="flex gap-3 p-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  {f.icon}
                </div>
                <div>
                  <p className="text-sm font-medium">{f.title}</p>
                  <p className="text-xs text-muted-foreground">{f.desc}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <Separator />

      {/* ───────── Step 1: Invite the Bot ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
            1
          </span>
          Invite the Bot to Your Server
        </h2>
        <p className="text-sm text-muted-foreground">
          Click the button below to add Jacky Music to your Discord server. You
          must have <strong>Manage Server</strong> permission on the server to
          invite bots.
        </p>
        {botInviteUrl && (
          <Button onClick={() => window.open(botInviteUrl, "_blank")}>
            <Bot className="mr-2 h-4 w-4" />
            Add Jacky Music to Discord
            <ExternalLink className="ml-1.5 h-3 w-3" />
          </Button>
        )}

        <Card>
          <CardContent className="p-4 space-y-3">
            <p className="text-sm font-medium flex items-center gap-2">
              <Shield className="h-4 w-4 text-primary" />
              Permissions & Role
            </p>
            <p className="text-xs text-muted-foreground">
              When you invite the bot, Discord will create a managed role named{" "}
              <Badge variant="secondary" className="text-xs font-mono mx-0.5">Jacky Music</Badge>{" "}
              with the following permissions:
            </p>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {[
                {
                  icon: <Eye className="h-3.5 w-3.5" />,
                  name: "View Channels",
                  why: "See text and voice channels",
                },
                {
                  icon: <MessageSquare className="h-3.5 w-3.5" />,
                  name: "Send Messages",
                  why: "Reply to commands with embeds",
                },
                {
                  icon: <Link2 className="h-3.5 w-3.5" />,
                  name: "Embed Links",
                  why: "Display rich embeds (now playing, queue, etc.)",
                },
                {
                  icon: <BookOpen className="h-3.5 w-3.5" />,
                  name: "Read Message History",
                  why: "View past messages in context",
                },
                {
                  icon: <Headphones className="h-3.5 w-3.5" />,
                  name: "Connect",
                  why: "Join voice channels",
                },
                {
                  icon: <Volume2 className="h-3.5 w-3.5" />,
                  name: "Speak",
                  why: "Stream audio in voice channels",
                },
              ].map((perm) => (
                <div
                  key={perm.name}
                  className="flex items-start gap-2 rounded-md border p-2"
                >
                  <div className="mt-0.5 text-primary">{perm.icon}</div>
                  <div>
                    <p className="text-xs font-medium">{perm.name}</p>
                    <p className="text-xs text-muted-foreground">{perm.why}</p>
                  </div>
                </div>
              ))}
            </div>

            <div className="rounded-md border border-yellow-500/30 bg-yellow-500/5 p-3 space-y-1">
              <p className="text-xs font-medium flex items-center gap-1.5">
                <Lock className="h-3.5 w-3.5 text-yellow-500" />
                Private Channels
              </p>
              <p className="text-xs text-muted-foreground">
                The bot can only see channels that its role has access to. If you
                want to use the bot in a <strong>private channel</strong>, you
                need to explicitly add the{" "}
                <Badge variant="secondary" className="text-xs font-mono mx-0.5">Jacky Music</Badge>{" "}
                role (or the bot user) to that channel's permission overrides.
                Go to <strong>Channel Settings &rarr; Permissions &rarr; Add members or roles</strong> and
                add the bot.
              </p>
            </div>
          </CardContent>
        </Card>
      </section>

      <Separator />

      {/* ───────── Step 2: Activate Your Server ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
            2
          </span>
          Activate Your Server
        </h2>
        <p className="text-sm text-muted-foreground">
          Before the bot responds to commands, you need to activate it for your
          server. This links your Discord server to the web dashboard.
        </p>
        <ol className="space-y-2 text-sm text-muted-foreground list-decimal list-inside">
          <li>
            Sign in with Google on the{" "}
            <button
              onClick={() => navigate("/")}
              className="underline underline-offset-2 text-foreground hover:text-primary"
            >
              home page
            </button>.
          </li>
          <li>
            Click{" "}
            <span className="font-medium text-foreground">Activate Server</span>.
          </li>
          <li>
            Enter your <strong>Discord Server ID</strong>.
            <p className="mt-1 ml-4 text-xs">
              To find it: open Discord &rarr; right-click your server name &rarr;{" "}
              <strong>Copy Server ID</strong>.
              <br />
              If you don't see this option, enable{" "}
              <strong>Developer Mode</strong> in Discord:{" "}
              <span className="text-foreground">
                Settings &rarr; App Settings &rarr; Advanced &rarr; Developer Mode
              </span>.
            </p>
          </li>
          <li>
            Click{" "}
            <span className="font-medium text-foreground">Activate</span> and
            you're done.
          </li>
        </ol>
      </section>

      <Separator />

      {/* ───────── Step 3: Start Playing ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
            3
          </span>
          Start Playing Music
        </h2>
        <p className="text-sm text-muted-foreground">
          Join a voice channel in Discord, then type a command in any text
          channel the bot can see. All commands use the{" "}
          <Badge variant="secondary" className="text-xs font-mono">j!</Badge>{" "}
          prefix.
        </p>

        <Card>
          <CardContent className="p-4 space-y-2">
            <p className="text-sm font-medium">Quick start</p>
            <div className="space-y-1.5">
              <CommandLine cmd="j!play lofi hip hop" desc="Search and play a song" />
              <CommandLine cmd="j!play https://youtube.com/watch?v=..." desc="Play a direct URL" />
              <CommandLine cmd="j!start" desc="Join voice and get a web dashboard session code" />
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              When the bot joins voice, it generates a <strong>6-character session code</strong>{" "}
              (e.g., <code className="rounded bg-muted px-1">A1B2C3</code>).
              Enter this code on the{" "}
              <button
                onClick={() => navigate("/")}
                className="underline underline-offset-2 text-foreground hover:text-primary"
              >
                home page
              </button>{" "}
              to open the web dashboard and control playback from your browser.
            </p>
          </CardContent>
        </Card>
      </section>

      <Separator />

      {/* ───────── Commands Reference ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Terminal className="h-5 w-5 text-primary" />
          Commands Reference
        </h2>
        <p className="text-sm text-muted-foreground">
          Type{" "}
          <code className="rounded bg-muted px-1 text-foreground">j!help</code>{" "}
          in Discord to see all commands. Here's a full reference:
        </p>

        <Card>
          <CardContent className="p-0">
            <CommandTable
              title="Playback"
              icon={<Headphones className="h-3.5 w-3.5" />}
              commands={[
                { cmd: "j!play <song>", aliases: "j!p", desc: "Play a song or add to queue (name or URL)" },
                { cmd: "j!start", aliases: "j!join", desc: "Join voice channel and get a web session code" },
                { cmd: "j!pause", desc: "Pause the current track" },
                { cmd: "j!resume", aliases: "j!unpause", desc: "Resume paused playback" },
                { cmd: "j!skip", aliases: "j!s", desc: "Skip to the next track" },
                { cmd: "j!stop", aliases: "j!leave, j!dc", desc: "Stop playback and leave voice" },
                { cmd: "j!volume <0-100>", aliases: "j!vol", desc: "Set playback volume" },
                { cmd: "j!loop", desc: "Cycle loop mode: off \u2192 track \u2192 queue" },
                { cmd: "j!nowplaying", aliases: "j!np", desc: "Show the currently playing track" },
              ]}
            />
            <Separator />
            <CommandTable
              title="Queue"
              icon={<Hash className="h-3.5 w-3.5" />}
              commands={[
                { cmd: "j!queue", aliases: "j!q", desc: "Show the current queue" },
                { cmd: "j!remove <position>", desc: "Remove a track by its position in the queue" },
                { cmd: "j!move <from> <to>", desc: "Move a track to a new position" },
                { cmd: "j!shuffle", desc: "Shuffle the queue" },
              ]}
            />
            <Separator />
            <CommandTable
              title="Playlists"
              icon={<BookOpen className="h-3.5 w-3.5" />}
              commands={[
                { cmd: "j!playlist save <name>", desc: "Save the current queue as a playlist" },
                { cmd: "j!playlist load <name>", desc: "Load a saved playlist into the queue" },
                { cmd: "j!playlist list", aliases: "j!pl ls", desc: "List all saved playlists" },
                { cmd: "j!playlist delete <name>", aliases: "j!pl del", desc: "Delete a saved playlist" },
              ]}
            />
            <Separator />
            <CommandTable
              title="Other"
              icon={<Globe className="h-3.5 w-3.5" />}
              commands={[
                { cmd: "j!session", desc: "Show the current web dashboard session code" },
                { cmd: "j!history", desc: "Show recent play sessions" },
                { cmd: "j!help", desc: "Show all available commands" },
              ]}
            />
          </CardContent>
        </Card>
      </section>

      <Separator />

      {/* ───────── Web Dashboard ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Globe className="h-5 w-5 text-primary" />
          Web Dashboard
        </h2>
        <p className="text-sm text-muted-foreground">
          The web dashboard at{" "}
          <a
            href="https://discord-bot-jacky-music.web.app/"
            className="underline underline-offset-2 text-foreground hover:text-primary"
          >
            discord-bot-jacky-music.web.app
          </a>{" "}
          lets you control the bot from any device with a browser. Everything
          syncs in real time — changes on the web are reflected in Discord
          instantly, and vice versa.
        </p>
        <p className="text-sm text-muted-foreground">
          From the dashboard you can:
        </p>
        <ul className="space-y-1 text-sm text-muted-foreground list-disc list-inside ml-2">
          <li>Play, pause, skip, seek, and adjust volume</li>
          <li>Search for songs and add them to the queue</li>
          <li>Reorder or remove queue items</li>
          <li>Save and load playlists</li>
          <li>View command history and listening history</li>
          <li>Connect or disconnect a local audio node</li>
        </ul>
      </section>

      <Separator />

      {/* ───────── Local Audio Node ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Zap className="h-5 w-5 text-primary" />
          Local Audio Node (Optional)
        </h2>
        <p className="text-sm text-muted-foreground">
          By default, the bot uses a cloud-hosted audio engine. If you want
          lower latency, you can run the audio engine locally on your own
          machine. The bot stays in the cloud — only the audio processing runs
          locally, resulting in a shorter path to Discord's voice servers.
        </p>

        <Card>
          <CardContent className="p-4 space-y-3">
            <p className="text-sm font-medium">Setup</p>
            <ol className="space-y-1.5 text-sm text-muted-foreground list-decimal list-inside">
              <li>
                Install{" "}
                <a
                  href="https://docs.docker.com/get-docker/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline underline-offset-2 text-foreground hover:text-primary"
                >
                  Docker Desktop
                </a>{" "}
                on your machine.
              </li>
              <li>
                Clone and run the setup script from{" "}
                <a
                  href="https://github.com/chlgustjr41/jacky-music-local"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline underline-offset-2 text-foreground hover:text-primary"
                >
                  jacky-music-local
                </a>:
                <div className="mt-1.5 ml-4 space-y-1">
                  <code className="block rounded bg-muted px-2 py-1 text-xs text-foreground">
                    git clone https://github.com/chlgustjr41/jacky-music-local.git
                  </code>
                  <code className="block rounded bg-muted px-2 py-1 text-xs text-foreground">
                    cd jacky-music-local && bash setup.sh
                  </code>
                  <p className="text-xs text-muted-foreground">
                    On Windows PowerShell, use{" "}
                    <code className="rounded bg-muted px-1">.\setup.ps1</code> instead.
                  </p>
                </div>
              </li>
              <li>
                The script prints a tunnel URL and password. Connect via Discord
                or the web dashboard:
                <div className="mt-1.5 ml-4 space-y-1">
                  <p className="text-xs">
                    <strong>Discord:</strong>{" "}
                    <code className="rounded bg-muted px-1 text-foreground">
                      j!localnode connect &lt;url&gt; &lt;password&gt;
                    </code>
                  </p>
                  <p className="text-xs">
                    <strong>Web dashboard:</strong> Click the{" "}
                    <Badge variant="secondary" className="text-xs mx-0.5">Cloud</Badge>{" "}
                    badge in the header and enter the URL and password.
                  </p>
                </div>
              </li>
            </ol>
            <p className="text-xs text-muted-foreground">
              The local node auto-shuts down after 15 minutes of inactivity. If
              the connection drops, the bot automatically falls back to the cloud
              engine and will attempt to reconnect in the background. To
              permanently disconnect, use{" "}
              <code className="rounded bg-muted px-1 text-foreground">
                j!localnode disconnect
              </code>{" "}
              or the web dashboard.
            </p>
          </CardContent>
        </Card>
      </section>

      <Separator />

      {/* ───────── Troubleshooting ───────── */}
      <section className="space-y-3">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          Troubleshooting
        </h2>
        <Card>
          <CardContent className="p-4 space-y-3">
            {[
              {
                q: "The bot doesn't respond to commands",
                a: "Make sure you've activated the server (Step 2) and that the bot has View Channels and Send Messages permissions in the channel you're using.",
              },
              {
                q: "The bot can't join my voice channel",
                a: "The bot needs Connect and Speak permissions for that voice channel. For private voice channels, add the Jacky Music role to the channel's permissions.",
              },
              {
                q: "I can't find the Server ID option",
                a: "Enable Developer Mode: Discord Settings \u2192 App Settings \u2192 Advanced \u2192 Developer Mode. Then right-click the server name and select Copy Server ID.",
              },
              {
                q: "Session code says 'invalid or expired'",
                a: "Session codes expire when the bot leaves the voice channel. Use j!start or j!play to create a new session.",
              },
              {
                q: "Bot works in some channels but not others",
                a: "The bot can only see channels its role has access to. For private channels, explicitly add the Jacky Music role in the channel's permission settings.",
              },
            ].map((item) => (
              <div key={item.q} className="space-y-0.5">
                <p className="text-sm font-medium">{item.q}</p>
                <p className="text-xs text-muted-foreground">{item.a}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      {/* Back button */}
      <div className="text-center pt-4">
        <Button variant="outline" onClick={() => navigate("/")}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Home
        </Button>
      </div>
    </div>
  );
}

/* ─── Helper components ─── */

function CommandLine({ cmd, desc }: { cmd: string; desc: string }) {
  return (
    <div className="flex items-start gap-2">
      <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs text-foreground">
        {cmd}
      </code>
      <span className="text-xs text-muted-foreground pt-0.5">{desc}</span>
    </div>
  );
}

function CommandTable({
  title,
  icon,
  commands,
}: {
  title: string;
  icon: React.ReactNode;
  commands: { cmd: string; aliases?: string; desc: string }[];
}) {
  return (
    <div className="p-4 space-y-2">
      <p className="text-sm font-medium flex items-center gap-1.5">
        <span className="text-primary">{icon}</span>
        {title}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="pb-1.5 pr-3 font-medium">Command</th>
              <th className="pb-1.5 pr-3 font-medium">Aliases</th>
              <th className="pb-1.5 font-medium">Description</th>
            </tr>
          </thead>
          <tbody>
            {commands.map((c) => (
              <tr key={c.cmd} className="border-b last:border-0">
                <td className="py-1.5 pr-3">
                  <code className="rounded bg-muted px-1 text-foreground whitespace-nowrap">
                    {c.cmd}
                  </code>
                </td>
                <td className="py-1.5 pr-3 text-muted-foreground whitespace-nowrap">
                  {c.aliases || "\u2014"}
                </td>
                <td className="py-1.5 text-muted-foreground">{c.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
