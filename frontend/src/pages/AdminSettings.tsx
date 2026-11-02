import { Link } from "react-router-dom";
import { ConfigurationPanel } from "../components/ConfigurationPanel";

export function AdminSettingsPage() {
  return <section className="page admin-settings-page"><div className="section-label"><div><Link to="/admin">Administration</Link><h1>Settings</h1></div><span>Sandbox · LLM</span></div><ConfigurationPanel mode="admin" /></section>;
}