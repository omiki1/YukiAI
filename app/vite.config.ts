import {defineConfig} from 'vite';
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const fullReplyFont=new URL('./src/fonts/fusion-pixel-12-zh_hans.woff2',import.meta.url);
const replyFont=fileURLToPath(existsSync(fullReplyFont)?fullReplyFont:new URL('./src/fonts/fusion-pixel-12-ui.woff2',import.meta.url));

export default defineConfig(({mode})=>({
 base:'./',define:{__YUKI_PAPER_ONLY__:mode==='alpha'},publicDir:mode==='alpha'?false:'public',
 // Public checkouts use the reviewed subset; an optional local full font stays private.
 resolve:{alias:{'./fonts/fusion-pixel-12-zh_hans.woff2':replyFont}},
 build:{target:'chrome83',outDir:mode==='alpha'?'dist-alpha':'dist',emptyOutDir:true}
}));
