import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function logout() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("pulseai_token");
  window.location.href = "/login";
}
