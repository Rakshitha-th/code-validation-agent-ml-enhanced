import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { createAdminUser, getAdminUsers, updateAdminUser } from "../api/admin";
import type { Role, User } from "../api/types";

const ROLES: Role[] = ["Developer", "Reviewer", "Technical Lead", "Administrator"];

export function AdminUsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("Developer");
  const [editingUserId, setEditingUserId] = useState("");
  const [editFullName, setEditFullName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function reload(signal?: AbortSignal) {
    setLoading(true);
    try { setUsers(await getAdminUsers(signal)); setError(""); }
    catch (reason) { if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "Users could not be loaded."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, []);

  async function createUser(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const created = await createAdminUser({ full_name: fullName.trim(), email: email.trim(), role, password });
      setUsers((items) => [...items, created]);
      setFullName(""); setEmail(""); setPassword(""); setRole("Developer"); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "User could not be created."); }
  }

  async function changeUser(user: User, changes: { readonly role?: Role; readonly active?: boolean }) {
    if (changes.role && !window.confirm(`Change ${user.full_name}'s role to ${changes.role}?`)) return;
    if (changes.active !== undefined && !window.confirm(`${changes.active ? "Activate" : "Deactivate"} ${user.full_name}?`)) return;
    try {
      const updated = await updateAdminUser(user.id, changes);
      setUsers((items) => items.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "User could not be updated."); }
  }

  async function saveProfile(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const updated = await updateAdminUser(editingUserId, { full_name: editFullName.trim(), email: editEmail.trim() });
      setUsers((items) => items.map((item) => item.id === updated.id ? updated : item));
      setEditingUserId(""); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "User profile could not be updated."); }
  }

  return <section className="page admin-users-page">
    <div className="section-label"><h1>Administration</h1><Link to="/admin/settings">Settings</Link></div>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <form className="admin-create-user" onSubmit={(event) => void createUser(event)}>
      <h2>Create user</h2>
      <label>Full name<input required value={fullName} onChange={(event) => setFullName(event.target.value)} /></label>
      <label>Email<input required type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label>Role<select value={role} onChange={(event) => setRole(event.target.value as Role)}>{ROLES.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Temporary password<input required type="password" minLength={12} autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <p>The password is hashed by the Gateway and is never returned by the API. Share it with the user through a secure channel.</p>
      <button className="btn btn--primary" type="submit">Create user</button>
    </form>
    <section><div className="section-label"><h2>Users</h2><span>{users.length} accounts</span></div>
      {loading && <p aria-live="polite">Loading users...</p>}
      {!loading && !error && users.length === 0 && <p className="empty-state">No users are available.</p>}
      {users.length > 0 && <div className="table-scroll"><table className="findings-table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Access</th><th>Actions</th></tr></thead><tbody>{users.map((user) => <tr key={user.id}><td>{user.full_name}</td><td>{user.email}</td><td><select aria-label={`Role for ${user.full_name}`} value={user.role} onChange={(event) => void changeUser(user, { role: event.target.value as Role })}>{ROLES.map((item) => <option key={item}>{item}</option>)}</select></td><td>{user.active === false ? "Inactive" : "Active"}</td><td><details onToggle={(event) => { if (event.currentTarget.open) { setEditingUserId(user.id); setEditFullName(user.full_name); setEditEmail(user.email); } }}><summary>Edit</summary><form className="admin-user-edit" onSubmit={(event) => void saveProfile(event)}><label>Full name<input required value={editingUserId === user.id ? editFullName : user.full_name} onChange={(event) => setEditFullName(event.target.value)} /></label><label>Email<input required type="email" value={editingUserId === user.id ? editEmail : user.email} onChange={(event) => setEditEmail(event.target.value)} /></label><button className="btn" type="submit">Save profile</button></form></details><button className="btn" onClick={() => void changeUser(user, { active: user.active === false })}>{user.active === false ? "Activate" : "Deactivate"}</button></td></tr>)}</tbody></table></div>}
    </section>
  </section>;
}