import React from 'react';

const paths = {
  mic: <><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></>,
  text: <><path d="M4 5h16M12 5v15M8 20h8"/></>,
  send: <><path d="m5 12 7-7 7 7M12 5v15"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
  chat: <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2v-10.5A8.5 8.5 0 0 1 10.5 3h2a8.5 8.5 0 0 1 8.5 8.5Z"/>,
  book: <><path d="M4 3h13a3 3 0 0 1 3 3v15H7a3 3 0 0 1-3-3V3ZM4 17h16M8 7h8M8 11h5"/></>,
  play: <path d="m8 4 13 8-13 8V4Z"/>,
  pause: <path d="M8 5v14M16 5v14"/>,
  stop: <rect x="6" y="6" width="12" height="12" rx="2"/>,
  logout: <><path d="M9 4H4v16h5M9 12h12m-5-5 5 5-5 5"/></>,
  more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
};
export function Icon({ name, size = 20 }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export function Mark() {
  return <span className="brand-mark" aria-hidden="true"><i/><i/><i/><i/></span>;
}
