import './themes.css';
export type Theme='classic'|'glass'|'paper'|'neon'|'pixel';
const KEY='yuki.theme',DEFAULT_THEME:Theme='pixel',THEMES:Theme[]=['classic','glass','paper','neon','pixel'];
// Qt's Chromium supports grid gap but needs spacing fallbacks for flex layouts.
const gapProbe=document.createElement('div');gapProbe.style.cssText='position:absolute;left:-9999px;display:flex;flex-direction:column;row-gap:1px;';
for(let index=0;index<2;index++){const child=document.createElement('div');child.style.height='1px';gapProbe.appendChild(child);}
document.body.appendChild(gapProbe);document.documentElement.classList.toggle('no-flex-gap',gapProbe.scrollHeight!==3);gapProbe.remove();
export function applyTheme(theme:string){const value=(THEMES as string[]).includes(theme)?theme as Theme:DEFAULT_THEME;
 if(value==='classic')delete document.documentElement.dataset.theme;else document.documentElement.dataset.theme=value;
 localStorage.setItem(KEY,value);const select=document.getElementById('theme-select') as HTMLSelectElement|null;if(select)select.value=value;return value;}
applyTheme(localStorage.getItem(KEY)||DEFAULT_THEME);
document.getElementById('theme-select')?.addEventListener('change',event=>applyTheme((event.target as HTMLSelectElement).value));
const PIXEL_REPLY='yuki.pixelReply';
function applyPixelReply(on:boolean){document.documentElement.classList.toggle('plain-reply',!on);localStorage.setItem(PIXEL_REPLY,on?'1':'0');const box=document.getElementById('pixel-reply') as HTMLInputElement|null;if(box)box.checked=on;}
applyPixelReply(localStorage.getItem(PIXEL_REPLY)!=='0');
document.getElementById('pixel-reply')?.addEventListener('change',event=>applyPixelReply((event.target as HTMLInputElement).checked));
