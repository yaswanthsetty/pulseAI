import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { logout as apiLogout } from "@/lib/api";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function logout() {
  apiLogout();
}
