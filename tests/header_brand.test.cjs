const {test} = require('node:test');
const assert = require('node:assert/strict'), fs = require('node:fs');
const html=fs.readFileSync('tam/web/index.src.html','utf8');
const css=fs.readFileSync('tam/web/styles.css','utf8');
const header=html.match(/<header\b[\s\S]*?<\/header>/)[0];
test('one TAO brand in header, not repeated in account rail',()=>{
 assert.equal((html.match(/id="taoBrand"/g)||[]).length,1);
 assert.match(header,/id="taoBrand"/); assert.match(header,/id="taoBrandText">Workspace<\/b>/);
 assert.doesNotMatch(html.match(/<aside\b[\s\S]*?<\/aside>/)[0],/TAO Workspace/);
});
test('TAO logo keeps the animated inline SVG mark',()=>{
 const logo=header.match(/<span class="brand-mark fx-logo" id="taoLogo"[\s\S]*?<\/span>/)[0];
 assert.doesNotMatch(header,/✈/); assert.match(logo,/<svg\b[^>]*viewBox="100 420 1050 470"/);
 assert.equal((logo.match(/class="tl-s"/g)||[]).length,2);
 assert.equal((logo.match(/class="tl-tri"/g)||[]).length,1);
 assert.doesNotMatch(logo,/<img\b/);
 assert.match(header,/aria-label="TAO 账号管理器/); assert.match(header,/id="mode"/);
});
test('brand and navigation use common tokens without animated clipping',()=>{
 assert.match(css,/\.brand\{[^}]*var\(--ink\)/);
 assert.match(css,/\.brand-mark\.fx-logo svg\{[^}]*height:24px[^}]*width:auto/);
 assert.doesNotMatch(css,/\.brand-mark\.fx-logo img\{/);
 assert.doesNotMatch(html,/navWheel|navCollapse|side-nav-caption|side-nav-version/);
 assert.match(header,/id="mainNav"/); assert.match(css,/\.nav-item em/);
});
test('mobile navigation remains available when desktop navigation is hidden',()=>{
 assert.match(html,/class="tabbar"[^>]*aria-label="底部导航"/);
 assert.match(css,/@media \(max-width:720px\)[\s\S]*\.tabbar\{display:flex/);
 assert.match(css,/safe-area-inset-bottom/);
});
