import {defineConfig} from 'vite';
export default defineConfig(({mode})=>({base:'./',define:{__YUKI_PAPER_ONLY__:mode==='alpha'},publicDir:mode==='alpha'?false:'public',build:{target:'chrome83',outDir:mode==='alpha'?'dist-alpha':'dist',emptyOutDir:true}}));
