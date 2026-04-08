import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import { EntryScreen } from "./components/EntryScreen";
import { Dashboard } from "./components/Dashboard";
import { ActivateServer } from "./components/ActivateServer";

function App() {
  return (
    <BrowserRouter>
      <Toaster
        position="bottom-right"
        theme="dark"
        expand
        gap={8}
        toastOptions={{
          style: {
            background: "hsl(var(--card))",
            color: "hsl(var(--card-foreground))",
            border: "1px solid hsl(var(--border))",
            boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
          },
        }}
      />
      <Routes>
        <Route path="/" element={<EntryScreen />} />
        <Route path="/dashboard/:sessionCode" element={<Dashboard />} />
        <Route path="/activate" element={<ActivateServer />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
