import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { refusesToRender } from "./lib/framing";

const root = document.getElementById("root")!;

if (refusesToRender()) {
  // Embedded in another site's frame: do not mount the app (see lib/framing).
  // The one thing offered is a link that reopens it as the top-level page.
  // Built with DOM calls and textContent, so nothing from the URL is parsed
  // as markup.
  const link = document.createElement("a");
  link.href = window.location.href;
  link.target = "_top";
  link.rel = "noopener";
  link.textContent = "Open Jacky Music in its own tab";
  root.replaceChildren(link);
} else {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
