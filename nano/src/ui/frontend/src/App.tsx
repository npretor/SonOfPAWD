import { Link, Route, Routes } from "react-router-dom";

import LogsDrawer from "./components/LogsDrawer";
import Dashboard from "./routes/Dashboard";
import Settings from "./routes/Settings";
import { useSocketBinding } from "./hooks/useHubState";

export default function App() {
  // Start the socket subscription once at the top level. Components just read
  // from the store; they don't touch the socket.
  useSocketBinding();

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">SonOfPAWD</Link>
        <nav>
          <Link to="/">Dashboard</Link>
          <Link to="/settings">Settings</Link>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
      <LogsDrawer />
    </div>
  );
}
