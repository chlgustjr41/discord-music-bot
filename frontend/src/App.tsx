import { BrowserRouter, Routes, Route } from "react-router-dom";
import { EntryScreen } from "./components/EntryScreen";
import { Dashboard } from "./components/Dashboard";
import { ActivateServer } from "./components/ActivateServer";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<EntryScreen />} />
        <Route path="/dashboard/:sessionCode" element={<Dashboard />} />
        <Route path="/activate" element={<ActivateServer />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
