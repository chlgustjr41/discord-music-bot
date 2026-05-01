import { useNavigate } from "react-router-dom";
import {
  Globe,
  List,
  BookOpen,
  Zap,
  Hash,
  ArrowRight,
  ExternalLink,
  Terminal,
  Bot,
  GitFork,
  Headphones,
} from "lucide-react";

const BOT_CLIENT_ID = import.meta.env.VITE_DISCORD_CLIENT_ID || "";
const BOT_INVITE_URL = BOT_CLIENT_ID
  ? `https://discord.com/oauth2/authorize?client_id=${BOT_CLIENT_ID}&permissions=3230720&scope=bot`
  : "https://discord.com/developers/applications";

// Gate for the local-node feature. Set VITE_SHOW_LOCAL_NODE="true" in the
// frontend env and rebuild to show the Local Audio Node feature card, its
// j!localnode commands, nav link, hero CTA, and dedicated setup section.
const SHOW_LOCAL_NODE = import.meta.env.VITE_SHOW_LOCAL_NODE === "true";
const GITHUB_BOT = "https://github.com/chlgustjr41/discord-music-bot";
const GITHUB_LOCAL = "https://github.com/chlgustjr41/jacky-music-local";

const EQ_BARS = [
  { delay: "0s", duration: "0.8s" },
  { delay: "0.1s", duration: "1.1s" },
  { delay: "0.2s", duration: "0.6s" },
  { delay: "0.05s", duration: "0.9s" },
  { delay: "0.3s", duration: "0.7s" },
  { delay: "0.15s", duration: "1.0s" },
  { delay: "0.25s", duration: "0.85s" },
  { delay: "0.08s", duration: "1.2s" },
  { delay: "0.18s", duration: "0.75s" },
  { delay: "0.35s", duration: "0.95s" },
  { delay: "0.12s", duration: "1.15s" },
  { delay: "0.22s", duration: "0.65s" },
];

const FEATURES = [
  {
    icon: Headphones,
    title: "Rich Music Playback",
    desc: "Stream from YouTube, SoundCloud, Bandcamp, and more. Full controls with loop modes, volume, and skip.",
    color: "#6c63ff",
  },
  {
    icon: Globe,
    title: "Live Web Dashboard",
    desc: "Real-time browser controls — play, pause, skip, seek, and manage queues without leaving your browser.",
    color: "#1df5c0",
  },
  {
    icon: List,
    title: "Queue Management",
    desc: "Drag-to-reorder your queue, remove tracks, and cycle through off, track, or queue loop modes.",
    color: "#ff6b6b",
  },
  {
    icon: BookOpen,
    title: "Playlists & History",
    desc: "Save any queue as a named playlist. Import from YouTube playlist URLs or load from history.",
    color: "#ffd93d",
  },
  ...(SHOW_LOCAL_NODE
    ? [
        {
          icon: Zap,
          title: "Local Audio Node",
          desc: "Run Lavalink on your machine via Cloudflare tunnel. Lower latency, no port forwarding, auto-shutdown.",
          color: "#1df5c0",
        },
      ]
    : []),
  {
    icon: Hash,
    title: "Session Codes",
    desc: "Every session generates a 6-character code. Share it to give anyone live access to the web dashboard.",
    color: "#6c63ff",
  },
];

const COMMANDS: [string, string][] = [
  ["j!play <query/URL>", "Play or queue a track"],
  ["j!pause", "Pause playback"],
  ["j!resume", "Resume playback"],
  ["j!skip", "Skip the current track"],
  ["j!stop", "Stop and disconnect"],
  ["j!nowplaying", "Current track info"],
  ["j!volume <0–100>", "Set volume level"],
  ["j!loop", "Cycle: off → track → queue"],
  ["j!queue [page]", "Show paginated queue"],
  ["j!shuffle", "Shuffle the queue"],
  ["j!remove <pos>", "Remove track by position"],
  ["j!move <from> <to>", "Reorder a track"],
  ["j!playlist save <name>", "Save queue as playlist"],
  ["j!playlist load <name>", "Load a saved playlist"],
  ["j!playlist list", "List all saved playlists"],
  ["j!history", "Recent play sessions"],
  ["j!session", "Show session code + link"],
  ...(SHOW_LOCAL_NODE
    ? ([
        ["j!localnode connect <url> <pw>", "Connect local audio node"],
        ["j!localnode disconnect", "Switch back to cloud"],
        ["j!localnode status", "Show audio backend"],
      ] as [string, string][])
    : []),
];

const BOT_STEPS = [
  {
    n: "01",
    title: "Invite the Bot",
    desc: "Click the invite button to add Jacky Music to your Discord server. Grant the required permissions when prompted.",
    code: null as string | null,
    cta: {
      label: "Invite to Discord",
      href: BOT_INVITE_URL,
      external: true,
    } as { label: string; href: string; external: boolean } | null,
  },
  {
    n: "02",
    title: "Sign In & Activate",
    desc: 'Open the web app, sign in with Google, then click "Activate Server" to link your Discord server.',
    code: null as string | null,
    cta: { label: "Activate Server", href: "/activate", external: false } as {
      label: string;
      href: string;
      external: boolean;
    } | null,
  },
  {
    n: "03",
    title: "Play Your First Track",
    desc: "Join a voice channel and play any song. The bot generates a 6-character session code automatically.",
    code: "j!play lofi hip hop radio",
    cta: null as { label: string; href: string; external: boolean } | null,
  },
  {
    n: "04",
    title: "Open the Dashboard",
    desc: "Run j!session to get your code, then enter it on the web app to open the real-time dashboard.",
    code: "j!session",
    cta: { label: "Enter Session Code", href: "/app", external: false } as {
      label: string;
      href: string;
      external: boolean;
    } | null,
  },
];

const LOCAL_STEPS = [
  {
    n: "01",
    title: "Clone the Repo",
    desc: "Download the local audio node setup. Requires Docker Desktop installed and running.",
    code: "git clone https://github.com/chlgustjr41/jacky-music-local.git\ncd jacky-music-local",
  },
  {
    n: "02",
    title: "Run Setup",
    desc: "One command starts Lavalink, opens a Cloudflare tunnel, and prints your connection command.",
    code: "# Windows PowerShell\n.\\setup.ps1\n\n# macOS / Linux / Git Bash\nbash setup.sh",
  },
  {
    n: "03",
    title: "Connect in Discord",
    desc: "Paste the j!localnode connect command printed by the setup script into your Discord server.",
    code: "j!localnode connect https://xxxx.trycloudflare.com <password>",
  },
];

const LANDING_STYLES = `
  @import url('https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

  .lp {
    background: #0b0c10;
    color: #e2e0f0;
    min-height: 100vh;
    font-family: 'Geist Variable', sans-serif;
    --lp-purple: #6c63ff;
    --lp-teal: #1df5c0;
    --lp-surface: #13151c;
    --lp-border: rgba(108,99,255,0.18);
    --lp-muted: #6b6b8a;
  }

  .lp-syne { font-family: 'Syne', 'Geist Variable', sans-serif; }
  .lp-mono { font-family: 'JetBrains Mono', 'Courier New', monospace; }

  @keyframes lp-eq {
    0%, 100% { transform: scaleY(0.12); }
    50% { transform: scaleY(1); }
  }
  .lp-eq-bar {
    transform-origin: bottom;
    animation: lp-eq ease-in-out infinite;
  }

  @keyframes lp-fade-up {
    from { opacity: 0; transform: translateY(22px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .lp-anim-1 { animation: lp-fade-up 0.65s ease 0.1s both; }
  .lp-anim-2 { animation: lp-fade-up 0.65s ease 0.25s both; }
  .lp-anim-3 { animation: lp-fade-up 0.65s ease 0.4s both; }

  .lp-nav {
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    background: rgba(11,12,16,0.85);
    border-bottom: 1px solid var(--lp-border);
    position: sticky;
    top: 0;
    z-index: 50;
  }
  .lp-nav-inner {
    max-width: 1100px;
    margin: 0 auto;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .lp-nav-links {
    display: flex;
    gap: 24px;
    align-items: center;
  }
  .lp-nav-link {
    color: var(--lp-muted);
    font-size: 14px;
    text-decoration: none;
    transition: color 0.15s;
  }
  .lp-nav-link:hover { color: #e2e0f0; }

  .lp-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(108,99,255,0.12);
    border: 1px solid rgba(108,99,255,0.28);
    border-radius: 100px;
    padding: 4px 14px;
    font-size: 11px;
    font-weight: 600;
    color: #a9a3ff;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .lp-badge-teal {
    background: rgba(29,245,192,0.1);
    border-color: rgba(29,245,192,0.28);
    color: #1df5c0;
  }

  .lp-gradient-text {
    background: linear-gradient(135deg, var(--lp-purple) 0%, var(--lp-teal) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    /* -webkit-background-clip clips paint to the line box, so glyphs with
       descenders (j, g, p, q, y) get truncated when the parent uses a tight
       line-height. display: inline-block + generous vertical padding and a
       taller line-height give the descender room to render without shifting
       baseline alignment of neighboring glyphs. */
    display: inline-block;
    padding: 0.05em 0 0.25em;
    line-height: 1.4;
    vertical-align: baseline;
  }

  .lp-btn-primary {
    background: var(--lp-purple);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 11px 22px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    transition: opacity 0.2s, transform 0.15s, box-shadow 0.2s;
    text-decoration: none;
    white-space: nowrap;
  }
  .lp-btn-primary:hover {
    opacity: 0.88;
    transform: translateY(-1px);
    box-shadow: 0 6px 24px rgba(108,99,255,0.4);
  }

  .lp-btn-outline {
    background: transparent;
    color: #e2e0f0;
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 8px;
    padding: 11px 22px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    transition: background 0.2s, border-color 0.2s, transform 0.15s;
    text-decoration: none;
    white-space: nowrap;
  }
  .lp-btn-outline:hover {
    background: rgba(255,255,255,0.07);
    border-color: rgba(255,255,255,0.3);
    transform: translateY(-1px);
  }

  .lp-section {
    max-width: 1100px;
    margin: 0 auto;
    padding: 100px 24px;
  }

  .lp-section-head {
    text-align: center;
    margin-bottom: 56px;
  }

  .lp-h2 {
    font-family: 'Syne', 'Geist Variable', sans-serif;
    font-weight: 700;
    font-size: clamp(30px, 4vw, 46px);
    color: #fff;
    letter-spacing: -0.02em;
    line-height: 1.25;
    margin: 16px 0 0;
    padding-bottom: 0.1em;
  }

  .lp-feature-card {
    background: var(--lp-surface);
    border: 1px solid var(--lp-border);
    border-radius: 14px;
    padding: 26px;
    transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  }
  .lp-feature-card:hover {
    transform: translateY(-3px);
  }

  .lp-terminal {
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid rgba(108,99,255,0.2);
  }
  .lp-terminal-header {
    background: #1a1b22;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .lp-terminal-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
  }
  .lp-terminal-body {
    background: #0d0e13;
    padding: 22px 24px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    line-height: 1.85;
    color: #c9d1d9;
    overflow-x: auto;
  }

  .lp-step-card {
    background: var(--lp-surface);
    border: 1px solid var(--lp-border);
    border-radius: 14px;
    padding: 26px;
    position: relative;
    overflow: hidden;
  }

  .lp-step-num {
    font-family: 'Syne', sans-serif;
    font-size: 80px;
    font-weight: 800;
    line-height: 1;
    background: linear-gradient(135deg, var(--lp-purple), transparent 70%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    opacity: 0.25;
    position: absolute;
    top: -8px;
    right: 6px;
    pointer-events: none;
    user-select: none;
  }

  .lp-code-block {
    background: #0d0e13;
    border: 1px solid rgba(108,99,255,0.18);
    border-radius: 8px;
    padding: 14px 18px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12.5px;
    line-height: 1.75;
    color: #c9d1d9;
    overflow-x: auto;
    margin-top: 16px;
  }

  .lp-arch-node {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    padding: 12px 18px;
    text-align: center;
    flex: 1;
    min-width: 120px;
  }

  .lp-divider {
    height: 1px;
    background: linear-gradient(to right, transparent, rgba(108,99,255,0.25), transparent);
    max-width: 1100px;
    margin: 0 auto;
  }

  .lp-tech-pill {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 100px;
    padding: 4px 12px;
    font-size: 11.5px;
    color: var(--lp-muted);
  }

  .lp-cmd-pair {
    display: contents;
  }

  @media (max-width: 700px) {
    .lp-nav-links a:not(:last-child) { display: none; }
    .lp-hero-eq { display: none !important; }
  }
`;

export function LandingPage() {
  const navigate = useNavigate();

  const goTo = (href: string) => navigate(href);

  return (
    <>
      <style>{LANDING_STYLES}</style>

      <div className="lp">
        {/* ── NAV ── */}
        <nav className="lp-nav">
          <div className="lp-nav-inner">
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                flexShrink: 0,
              }}
            >
              <img src="/favicon.svg" alt="Jacky Music" style={{ width: 32, height: 32 }} />
              <span
                className="lp-syne"
                style={{ fontWeight: 700, fontSize: 17, color: "#fff" }}
              >
                Jacky Music
              </span>
            </div>

            <div className="lp-nav-links">
              <a href="#features" className="lp-nav-link">
                Features
              </a>
              <a href="#commands" className="lp-nav-link">
                Commands
              </a>
              <a href="#setup" className="lp-nav-link">
                Setup
              </a>
              {SHOW_LOCAL_NODE && (
                <a href="#setup-local" className="lp-nav-link">
                  Local Node
                </a>
              )}
              <a
                href={BOT_INVITE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="lp-btn-primary"
                style={{ padding: "8px 16px", fontSize: 13 }}
              >
                <Bot style={{ width: 14, height: 14 }} />
                Invite Bot
              </a>
            </div>
          </div>
        </nav>

        {/* ── HERO ── */}
        <section
          style={{
            position: "relative",
            overflow: "hidden",
            minHeight: "92vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {/* Purple radial glow */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "radial-gradient(ellipse 75% 55% at 50% 40%, rgba(108,99,255,0.14) 0%, transparent 70%)",
              pointerEvents: "none",
            }}
          />

          {/* Left equalizer */}
          <div
            className="lp-hero-eq"
            style={{
              position: "absolute",
              left: "7%",
              bottom: "22%",
              display: "flex",
              gap: 5,
              alignItems: "flex-end",
              height: 110,
            }}
          >
            {EQ_BARS.slice(0, 9).map((bar, i) => (
              <div
                key={i}
                className="lp-eq-bar"
                style={{
                  width: 5,
                  height: 110,
                  background:
                    "linear-gradient(to top, var(--lp-purple), rgba(108,99,255,0.2))",
                  borderRadius: 3,
                  animationDelay: bar.delay,
                  animationDuration: bar.duration,
                  opacity: 0.55,
                }}
              />
            ))}
          </div>

          {/* Right equalizer */}
          <div
            className="lp-hero-eq"
            style={{
              position: "absolute",
              right: "7%",
              bottom: "22%",
              display: "flex",
              gap: 5,
              alignItems: "flex-end",
              height: 110,
            }}
          >
            {EQ_BARS.slice(3, 12).map((bar, i) => (
              <div
                key={i}
                className="lp-eq-bar"
                style={{
                  width: 5,
                  height: 110,
                  background:
                    "linear-gradient(to top, var(--lp-teal), rgba(29,245,192,0.2))",
                  borderRadius: 3,
                  animationDelay: bar.delay,
                  animationDuration: bar.duration,
                  opacity: 0.45,
                }}
              />
            ))}
          </div>

          {/* Hero content */}
          <div
            style={{
              textAlign: "center",
              padding: "0 24px",
              maxWidth: 680,
              position: "relative",
              zIndex: 1,
            }}
          >
            <div className="lp-anim-1">
              <span
                className="lp-badge"
                style={{ marginBottom: 28, display: "inline-flex" }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "var(--lp-teal)",
                  }}
                />
                Discord Music Bot
              </span>
              <h1
                className="lp-syne"
                style={{
                  fontSize: "clamp(56px, 10vw, 104px)",
                  lineHeight: 1.0,
                  letterSpacing: "-0.035em",
                  color: "#fff",
                  margin: "0 0 8px",
                  fontWeight: 800,
                }}
              >
                Jacky <span className="lp-gradient-text">Music</span>
              </h1>
            </div>

            <p
              className="lp-anim-2"
              style={{
                fontSize: "clamp(16px, 2.2vw, 19px)",
                color: "var(--lp-muted)",
                lineHeight: 1.65,
                margin: "20px auto 40px",
                maxWidth: 500,
              }}
            >
              A Discord music bot with a real-time web dashboard. Play music via
              commands or control everything from your browser — always in sync.
            </p>

            <div
              className="lp-anim-3"
              style={{
                display: "flex",
                gap: 12,
                justifyContent: "center",
                flexWrap: "wrap",
              }}
            >
              <a
                href={BOT_INVITE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="lp-btn-primary"
              >
                <Bot style={{ width: 17, height: 17 }} />
                Invite the Bot
                <ExternalLink style={{ width: 13, height: 13, opacity: 0.7 }} />
              </a>
              {SHOW_LOCAL_NODE && (
                <a href="#setup-local" className="lp-btn-outline">
                  <Terminal style={{ width: 17, height: 17 }} />
                  Local Audio Setup
                </a>
              )}
              <button
                onClick={() => goTo("/app")}
                className="lp-btn-outline"
                style={{ borderColor: "rgba(29,245,192,0.25)" }}
              >
                Open Dashboard
                <ArrowRight style={{ width: 15, height: 15 }} />
              </button>
            </div>
          </div>

          {/* Full-width animated equalizer strip at bottom */}
          <div
            style={{
              position: "absolute",
              bottom: 0,
              left: 0,
              right: 0,
              height: 52,
              display: "flex",
              gap: 2,
              alignItems: "flex-end",
              opacity: 0.18,
            }}
          >
            {Array.from({ length: 64 }, (_, i) => (
              <div
                key={i}
                className="lp-eq-bar"
                style={{
                  flex: 1,
                  height: 52,
                  background:
                    i % 3 === 0
                      ? "var(--lp-purple)"
                      : i % 3 === 1
                        ? "var(--lp-teal)"
                        : "#ffffff",
                  borderRadius: "2px 2px 0 0",
                  animationDelay: `${((i * 0.045) % 0.85).toFixed(2)}s`,
                  animationDuration: `${(0.55 + (i % 8) * 0.09).toFixed(2)}s`,
                }}
              />
            ))}
          </div>
        </section>

        {/* ── FEATURES ── */}
        <section id="features" className="lp-section">
          <div className="lp-section-head">
            <span className="lp-badge">Features</span>
            <h2 className="lp-h2">Everything for your server's music</h2>
            <p
              style={{
                color: "var(--lp-muted)",
                fontSize: 16,
                maxWidth: 480,
                margin: "16px auto 0",
                lineHeight: 1.65,
              }}
            >
              From Discord commands to a full web dashboard, every part of the
              experience is covered.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(290px, 1fr))",
              gap: 18,
            }}
          >
            {FEATURES.map((f) => {
              const Icon = f.icon;
              return (
                <div
                  key={f.title}
                  className="lp-feature-card"
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLDivElement).style.borderColor =
                      f.color + "44";
                    (e.currentTarget as HTMLDivElement).style.boxShadow =
                      `0 8px 32px ${f.color}1a`;
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLDivElement).style.borderColor = "";
                    (e.currentTarget as HTMLDivElement).style.boxShadow = "";
                  }}
                >
                  <div
                    style={{
                      width: 42,
                      height: 42,
                      borderRadius: 10,
                      background: f.color + "1a",
                      border: `1px solid ${f.color}30`,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      marginBottom: 16,
                    }}
                  >
                    <Icon style={{ width: 19, height: 19, color: f.color }} />
                  </div>
                  <h3
                    style={{
                      fontSize: 15,
                      fontWeight: 600,
                      color: "#fff",
                      marginBottom: 8,
                    }}
                  >
                    {f.title}
                  </h3>
                  <p
                    style={{
                      fontSize: 13.5,
                      color: "var(--lp-muted)",
                      lineHeight: 1.65,
                    }}
                  >
                    {f.desc}
                  </p>
                </div>
              );
            })}
          </div>
        </section>

        <div className="lp-divider" />

        {/* ── COMMANDS ── */}
        <section
          id="commands"
          className="lp-section"
          style={{ paddingTop: 80, paddingBottom: 80 }}
        >
          <div className="lp-section-head">
            <span className="lp-badge">Reference</span>
            <h2 className="lp-h2">
              All{" "}
              <code
                className="lp-mono"
                style={{
                  color: "var(--lp-purple)",
                  background: "rgba(108,99,255,0.12)",
                  padding: "2px 7px",
                  borderRadius: 4,
                  fontSize: 38,
                }}
              >
                j!
              </code>{" "}
              commands
            </h2>
            <p
              style={{ color: "var(--lp-muted)", fontSize: 16, marginTop: 14 }}
            >
              Every command uses the{" "}
              <code
                className="lp-mono"
                style={{
                  color: "var(--lp-purple)",
                  background: "rgba(108,99,255,0.12)",
                  padding: "2px 7px",
                  borderRadius: 4,
                  fontSize: 13,
                }}
              >
                j!
              </code>{" "}
              prefix.
            </p>
          </div>

          <div className="lp-terminal">
            <div className="lp-terminal-header">
              <div
                className="lp-terminal-dot"
                style={{ background: "#ff5f57" }}
              />
              <div
                className="lp-terminal-dot"
                style={{ background: "#febc2e" }}
              />
              <div
                className="lp-terminal-dot"
                style={{ background: "#28c840" }}
              />
              <span
                style={{
                  marginLeft: 10,
                  color: "var(--lp-muted)",
                  fontSize: 11.5,
                  fontFamily: "JetBrains Mono, monospace",
                }}
              >
                discord — Jacky Music commands
              </span>
            </div>
            <div className="lp-terminal-body">
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))",
                  gap: "0 40px",
                }}
              >
                {COMMANDS.map(([cmd, desc]) => (
                  <div
                    key={cmd}
                    style={{
                      display: "flex",
                      gap: 16,
                      padding: "7px 0",
                      borderBottom: "1px solid rgba(255,255,255,0.04)",
                    }}
                  >
                    <code
                      className="lp-mono"
                      style={{
                        fontSize: 13,
                        color: "#c9d1d9",
                        minWidth: 210,
                        flexShrink: 0,
                        whiteSpace: "nowrap",
                      }}
                    >
                      <span style={{ color: "var(--lp-purple)" }}>j!</span>
                      {cmd.slice(2)}
                    </code>
                    <span style={{ fontSize: 13, color: "var(--lp-muted)" }}>
                      {desc}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <div className="lp-divider" />

        {/* ── SETUP: BOT INVITE ── */}
        <section id="setup" className="lp-section" style={{ paddingTop: 80 }}>
          <div className="lp-section-head">
            <span className="lp-badge">
              <Bot style={{ width: 10, height: 10 }} />
              Bot Setup
            </span>
            <h2 className="lp-h2">Add Jacky Music to your server</h2>
            <p
              style={{
                color: "var(--lp-muted)",
                fontSize: 16,
                marginTop: 14,
                maxWidth: 440,
                margin: "14px auto 0",
              }}
            >
              Up and running in under 5 minutes. No config files or self-hosting
              required.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
              gap: 18,
            }}
          >
            {BOT_STEPS.map((step) => (
              <div key={step.n} className="lp-step-card">
                <span className="lp-step-num">{step.n}</span>
                <div
                  className="lp-mono"
                  style={{
                    fontSize: 11,
                    color: "var(--lp-purple)",
                    fontWeight: 600,
                    marginBottom: 10,
                    letterSpacing: "0.06em",
                  }}
                >
                  STEP {step.n}
                </div>
                <h3
                  style={{
                    fontSize: 16,
                    fontWeight: 600,
                    color: "#fff",
                    marginBottom: 10,
                  }}
                >
                  {step.title}
                </h3>
                <p
                  style={{
                    fontSize: 13.5,
                    color: "var(--lp-muted)",
                    lineHeight: 1.65,
                  }}
                >
                  {step.desc}
                </p>
                {step.code && (
                  <div className="lp-code-block">
                    <span style={{ color: "var(--lp-teal)" }}>$</span>{" "}
                    {step.code}
                  </div>
                )}
                {step.cta && (
                  <div style={{ marginTop: 18 }}>
                    {step.cta.external ? (
                      <a
                        href={step.cta.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="lp-btn-primary"
                        style={{ fontSize: 13, padding: "8px 16px" }}
                      >
                        {step.cta.label}
                        <ExternalLink style={{ width: 12, height: 12 }} />
                      </a>
                    ) : (
                      <button
                        onClick={() => goTo(step.cta!.href)}
                        className="lp-btn-primary"
                        style={{ fontSize: 13, padding: "8px 16px" }}
                      >
                        {step.cta.label}
                        <ArrowRight style={{ width: 12, height: 12 }} />
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {SHOW_LOCAL_NODE && (
          <div className="lp-divider" style={{ margin: "60px auto" }} />
        )}

        {/* ── SETUP: LOCAL NODE ── */}
        {SHOW_LOCAL_NODE && (
          <section
            id="setup-local"
            className="lp-section"
            style={{ paddingTop: 20, paddingBottom: 100 }}
          >
            <div className="lp-section-head">
              <span className="lp-badge lp-badge-teal">
                <Zap style={{ width: 10, height: 10 }} />
                Local Audio Node
              </span>
              <h2 className="lp-h2">
                Lower latency with a{" "}
                <span style={{ color: "var(--lp-teal)" }}>local node</span>
              </h2>
              <p
                style={{
                  color: "var(--lp-muted)",
                  fontSize: 16,
                  maxWidth: 500,
                  margin: "14px auto 0",
                  lineHeight: 1.65,
                }}
              >
                Run the audio engine on your own machine. A Cloudflare tunnel
                exposes it securely — no port forwarding or firewall changes
                needed.
              </p>
            </div>

            {/* Architecture diagram */}
            <div
              style={{
                background: "var(--lp-surface)",
                border: "1px solid var(--lp-border)",
                borderRadius: 12,
                padding: "24px 28px",
                marginBottom: 52,
              }}
            >
              <p
                style={{
                  fontSize: 11,
                  color: "var(--lp-muted)",
                  textAlign: "center",
                  marginBottom: 20,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  fontFamily: "JetBrains Mono, monospace",
                }}
              >
                Audio path with local node
              </p>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 10,
                  flexWrap: "wrap",
                }}
              >
                {(
                  [
                    {
                      label: "Your Machine",
                      sub: "Lavalink audio",
                      color: "var(--lp-teal)",
                    },
                    { label: "→", sub: null, color: null },
                    {
                      label: "Cloudflare Tunnel",
                      sub: "Secure exposure",
                      color: "var(--lp-muted)",
                    },
                    { label: "→", sub: null, color: null },
                    {
                      label: "Jacky Music Bot",
                      sub: "GCP cloud",
                      color: "var(--lp-purple)",
                    },
                    { label: "→", sub: null, color: null },
                    {
                      label: "Discord Voice",
                      sub: "Audio stream",
                      color: "#ffd93d",
                    },
                  ] as {
                    label: string;
                    sub: string | null;
                    color: string | null;
                  }[]
                ).map((item, i) =>
                  item.label === "→" ? (
                    <span
                      key={i}
                      style={{ color: "var(--lp-muted)", fontSize: 18 }}
                    >
                      →
                    </span>
                  ) : (
                    <div key={i} className="lp-arch-node">
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: item.color ?? "inherit",
                        }}
                      >
                        {item.label}
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: "var(--lp-muted)",
                          marginTop: 3,
                        }}
                      >
                        {item.sub}
                      </div>
                    </div>
                  ),
                )}
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                gap: 28,
              }}
            >
              {LOCAL_STEPS.map((step) => (
                <div key={step.n}>
                  <div
                    className="lp-mono"
                    style={{
                      fontSize: 11,
                      color: "var(--lp-teal)",
                      fontWeight: 600,
                      marginBottom: 10,
                      letterSpacing: "0.06em",
                    }}
                  >
                    STEP {step.n}
                  </div>
                  <h3
                    style={{
                      fontSize: 17,
                      fontWeight: 600,
                      color: "#fff",
                      marginBottom: 10,
                    }}
                  >
                    {step.title}
                  </h3>
                  <p
                    style={{
                      fontSize: 13.5,
                      color: "var(--lp-muted)",
                      lineHeight: 1.65,
                      marginBottom: 0,
                    }}
                  >
                    {step.desc}
                  </p>
                  <div className="lp-terminal" style={{ marginTop: 16 }}>
                    <div
                      className="lp-terminal-header"
                      style={{ padding: "8px 12px" }}
                    >
                      <div
                        className="lp-terminal-dot"
                        style={{ background: "#ff5f57", width: 8, height: 8 }}
                      />
                      <div
                        className="lp-terminal-dot"
                        style={{ background: "#febc2e", width: 8, height: 8 }}
                      />
                      <div
                        className="lp-terminal-dot"
                        style={{ background: "#28c840", width: 8, height: 8 }}
                      />
                    </div>
                    <div
                      className="lp-terminal-body"
                      style={{ padding: "14px 18px", fontSize: 12.5 }}
                    >
                      {step.code.split("\n").map((line, i) => (
                        <div key={i}>
                          {line === "" ? (
                            <br />
                          ) : line.startsWith("#") ? (
                            <span style={{ color: "#555" }}>{line}</span>
                          ) : (
                            <span>
                              <span style={{ color: "var(--lp-teal)" }}>
                                ${" "}
                              </span>
                              <span>{line}</span>
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div style={{ textAlign: "center", marginTop: 52 }}>
              <a
                href={GITHUB_LOCAL}
                target="_blank"
                rel="noopener noreferrer"
                className="lp-btn-outline"
              >
                <GitFork style={{ width: 16, height: 16 }} />
                jacky-music-local on GitHub
                <ExternalLink style={{ width: 13, height: 13, opacity: 0.6 }} />
              </a>
            </div>
          </section>
        )}

        {/* ── FOOTER ── */}
        <footer
          style={{
            borderTop: "1px solid var(--lp-border)",
            padding: "44px 24px",
            maxWidth: 1100,
            margin: "0 auto",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 20,
              marginBottom: 28,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <img src="/favicon.svg" alt="Jacky Music" style={{ width: 28, height: 28 }} />
              <span
                className="lp-syne"
                style={{ fontWeight: 700, color: "#fff", fontSize: 15 }}
              >
                Jacky Music
              </span>
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {[
                "Python 3.11",
                "discord.py",
                "Lavalink v4",
                "React 19",
                "Firebase",
                "Cloudflare",
              ].map((t) => (
                <span key={t} className="lp-tech-pill">
                  {t}
                </span>
              ))}
            </div>

            <div style={{ display: "flex", gap: 18 }}>
              <a
                href={GITHUB_BOT}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: "var(--lp-muted)",
                  fontSize: 13,
                  textDecoration: "none",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  transition: "color 0.15s",
                }}
                onMouseEnter={(e) =>
                  ((e.currentTarget as HTMLAnchorElement).style.color =
                    "#e2e0f0")
                }
                onMouseLeave={(e) =>
                  ((e.currentTarget as HTMLAnchorElement).style.color =
                    "var(--lp-muted)")
                }
              >
                <GitFork style={{ width: 14, height: 14 }} />
                discord-music-bot
              </a>
              <a
                href={GITHUB_LOCAL}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: "var(--lp-muted)",
                  fontSize: 13,
                  textDecoration: "none",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  transition: "color 0.15s",
                }}
                onMouseEnter={(e) =>
                  ((e.currentTarget as HTMLAnchorElement).style.color =
                    "#e2e0f0")
                }
                onMouseLeave={(e) =>
                  ((e.currentTarget as HTMLAnchorElement).style.color =
                    "var(--lp-muted)")
                }
              >
                <GitFork style={{ width: 14, height: 14 }} />
                jacky-music-local
              </a>
            </div>
          </div>

          <div
            style={{
              borderTop: "1px solid rgba(255,255,255,0.05)",
              paddingTop: 24,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 12,
            }}
          >
            <span style={{ fontSize: 12.5, color: "var(--lp-muted)" }}>
              Private project by Jacob Choi
            </span>
            <div style={{ display: "flex", gap: 16 }}>
              <button
                onClick={() => goTo("/app")}
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--lp-muted)",
                  fontSize: 12.5,
                  cursor: "pointer",
                  padding: 0,
                  textDecoration: "none",
                }}
              >
                Open Dashboard
              </button>
              <button
                onClick={() => goTo("/guide")}
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--lp-muted)",
                  fontSize: 12.5,
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                Setup Guide
              </button>
              <a
                href={BOT_INVITE_URL}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: "var(--lp-muted)",
                  fontSize: 12.5,
                  textDecoration: "none",
                }}
              >
                Invite Bot
              </a>
            </div>
          </div>
        </footer>
      </div>
    </>
  );
}
