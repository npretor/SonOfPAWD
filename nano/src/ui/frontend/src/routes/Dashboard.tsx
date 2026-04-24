import ModeSelect from "../components/ModeSelect";
import Sidebar from "../components/Sidebar";
import VideoPane from "../components/VideoPane";

export default function Dashboard() {
  return (
    <div className="dashboard">
      <Sidebar />
      <main className="main">
        <div className="main-header">
          <ModeSelect />
        </div>
        <VideoPane />
      </main>
    </div>
  );
}
