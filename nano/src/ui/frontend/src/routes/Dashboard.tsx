import AimToggle from "../components/AimToggle";
import ArmToggle from "../components/ArmToggle";
import ControlPad from "../components/ControlPad";
import FireButton from "../components/FireButton";
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
          <div className="header-spacer" />
          <AimToggle />
          <ArmToggle />
          <FireButton />
        </div>
        <VideoPane />
        <ControlPad />
      </main>
    </div>
  );
}
