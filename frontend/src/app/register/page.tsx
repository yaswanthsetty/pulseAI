"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { register, login, getAccessToken } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { if (getAccessToken()) router.replace("/search"); }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (name.trim().length < 2) { setError("Name must be at least 2 characters"); return; }
    if (!email.includes("@")) { setError("Please enter a valid email"); return; }
    if (password.length < 6) { setError("Password must be at least 6 characters"); return; }
    setLoading(true);
    try {
      await register(email, password, name);
      await login(email, password);
      router.replace("/search");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="w-12 h-12 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto mb-4">
            <span className="text-primary font-display font-bold text-lg">P</span>
          </div>
          <h1 className="text-xl font-display font-semibold text-foreground">Create account</h1>
          <p className="text-sm text-muted mt-1">Get started with PulseAI</p>
        </div>
        <form onSubmit={handleSubmit} className="bg-card border border-border/40 rounded-2xl p-6">
          {error && <div role="alert" className="mb-4 p-3 bg-destructive/10 border border-destructive/30 rounded-xl text-sm text-destructive">{error}</div>}
          <div className="mb-4">
            <label htmlFor="name" className="block text-xs font-medium text-muted mb-1.5">Name</label>
            <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required className="w-full bg-secondary/50 rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30 transition-colors" placeholder="Your name" />
          </div>
          <div className="mb-4">
            <label htmlFor="email" className="block text-xs font-medium text-muted mb-1.5">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required className="w-full bg-secondary/50 rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30 transition-colors" placeholder="you@example.com" />
          </div>
          <div className="mb-6">
            <label htmlFor="password" className="block text-xs font-medium text-muted mb-1.5">Password</label>
            <div className="relative">
              <input id="password" type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required className="w-full bg-secondary/50 rounded-xl px-4 py-3 pr-10 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30 transition-colors" placeholder="Min 6 characters" />
              <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-foreground transition-colors" aria-label={showPassword ? "Hide password" : "Show password"}>
                {showPassword ? <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12c1.292 4.338 5.31 7.5 10.066 7.5.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" /></svg> : <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" /><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>}
              </button>
            </div>
          </div>
          <button type="submit" disabled={loading} className="w-full py-3 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed transition-all">
            {loading ? "Creating account…" : "Create account"}
          </button>
        </form>
        <p className="text-center text-xs text-muted mt-4">
          Already have an account? <a href="/login" className="text-primary hover:text-primary-hover transition-colors">Sign in</a>
        </p>
      </div>
    </div>
  );
}
