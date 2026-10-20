// Qt 5.15 ships an older Chromium than the system browser. Preserve the existing UI API.
for(const prototype of [Element.prototype,DocumentFragment.prototype]){
 if(typeof prototype.replaceChildren!=='function')Object.defineProperty(prototype,'replaceChildren',{
  configurable:true,writable:true,
  value:function(...nodes:(Node|string)[]){const content=document.createDocumentFragment();content.append(...nodes);this.textContent='';this.appendChild(content);}
 });
}
