/* /demo — the open door: search the shared sample corpus.
 *
 * Read-only and stateless by design: no sessions, no uploads, nothing saved.
 * Anonymous callers are the default tenant, which OWNS the samples, so no
 * sign-in is needed to search them.
 *
 * The page is one screen: the corpus on the right of the hero — a slider that
 * cycles the ten videos and can be dragged, with a strip and a browse drawer
 * beside it — the ask box on the left, and everything the server does to answer
 * laid out as a trace of pills. There is deliberately no recent-questions strip
 * — one answer is on screen at a time and asking again replaces it.
 */
let CITES=[], SAMPLE_IDS=[], SAMPLE_NAME="the video";

/* Same hero treatment as the landing page: the coral words write themselves in
   behind a nib, then sign off with the underline. .ink is what gets uncovered. */
const UNDERLINE = `<svg viewBox="0 0 320 14" preserveAspectRatio="none"><path d="M4 9C70 3 154 2 316 6"/></svg>`;

/* YouTube titles carry a channel/chapter tail after a pipe or dash that adds
   nothing here, and a 60-character headline in Fraunces 5xl is a wall. Trim to
   the first clause and cut on a word boundary. */
function shortName(t){
  let s=String(t||"").split(/\s+[|·—]\s+/)[0].replace(/\s*[-–]\s*YouTube$/i,"").trim();
  if(s.length>44) s=s.slice(0,44).replace(/\s+\S*$/,"")+"…";
  return s || String(t||"the video");
}

/* The headline names the video it will answer about — "this video" told you
   nothing you couldn't already see. The written-in coral phrase stays on the
   fixed words: .mark is white-space:nowrap and its nib and underline are sized
   to one line, so a wrapping title can't carry the pen. The name sits on its
   own line at a size that survives a long one. */
function writeTitle(name, tail){
  const pen = `<span class="word" style="--i:1"><span class="mark text-coral"><span class="ink">hard question</span><i class="nib" aria-hidden="true"></i>${UNDERLINE}</span></span>`;
  // Only the NAME is quoted. "3Blue1Brown videos" inside the quotes reads as the
  // title of something; the quotes belong around what the thing is called, and
  // the common noun after them belongs to our own sentence.
  const named = `<span class="text-ink">“${esc(name)}”</span>${tail?` ${esc(tail)}`:""}`;
  $("#heroTitle").innerHTML =
    `<span class="word" style="--i:0">Ask a</span> ${pen}<br>`
    + `<span class="word block text-2xl sm:text-3xl mt-2 text-muted font-semibold leading-snug" style="--i:2">`
    + `about ${named}</span>`;
}
/* Written once, by loadSample() below, so the pen doesn't run twice: the h1's
   min-h holds its place for the one request it takes to learn the name. */

wireModal();
wireNav();   // top-right → the workspace

/* ---------- which model is answering ---------- */
(async function loadConfig(){
  try{
    const c=await apiJSON("/api/config"); const b=$("#llmBadge");
    if(c.llm_configured){
      b.textContent="LLM: "+(c.llm_model||c.llm_provider);
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#cfe8d6] text-[#1f7a43]";
    } else {
      b.textContent="No LLM, moments only";
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#e7c46a] text-[#8a6d1a] bg-[#fbf2d8]";
    }
  }catch{ $("#llmBadge").textContent="API offline"; }
})();

/* ---------- the video in the hero ----------
   The headline, the frame and the caption under it ARE the sample corpus — read
   from /api/videos rather than hardcoded, so swapping src/samples.py changes
   this page with no edit here. The still is the video's own YouTube thumbnail.

   maxresdefault is the clean 16:9 frame but doesn't exist for every video;
   hqdefault always does, so it's the fallback. */
const ytOf = v => (v && v.id || "").startsWith("yt_") ? v.id.slice(3) : null;

/* Every slide IS a play button — it opens the same player modal a citation
   opens, from 0:00. Playing needs no index, so it works even while a video is
   still embedding. */

/* Who made it and where it lives, said plainly and linked. We index public
   video, we don't host or own it: the creator gets their name (linked to their
   channel) and the original video is one click away. `author` comes from the
   curated corpus in src/samples.py — YouTube's own title replaces ours at
   ingest, so it's the only place the channel survives. */
const LINK="text-ink font-600 hover:text-coral2 underline decoration-line hover:decoration-coral";
function renderSource(v){
  const p=$("#heroSource");
  if(!v.url && !v.author){ p.classList.add("hidden"); return; }
  const video = v.url
    ? `<a href="${esc(v.url)}" target="_blank" rel="noopener" class="${LINK}"
         title="Open the original video">${v.source==="youtube"?"YouTube":esc(shortUrl(v.url))} ↗</a>`
    : "";
  const who = v.author
    ? (v.author_url
        ? `<a href="${esc(v.author_url)}" target="_blank" rel="noopener" class="${LINK}"
             title="${esc(v.author)} on YouTube">${esc(v.author)} ↗</a>`
        : `<span class="text-ink font-600">${esc(v.author)}</span>`)
    : "";
  // One line, no disclaimer tail: the footer strip already says nothing here is
  // saved, and a second line of small print just orphaned a word.
  p.innerHTML = who
    ? `Video by ${who}${video ? ` on ${video}` : ""}`
    : `Sourced from ${video}`;
  p.classList.remove("hidden");
}

/* ══════════════════════════════════════════════════════════════════════════
   THE CORPUS — strip, slider, drawer
   Three views of one list (/api/videos, samples only). The strip proves the
   corpus exists, the slider shows it, the drawer browses it. All three share
   CORPUS and one notion of which video is showing (CUR), so picking a row in
   the drawer moves the slider instead of opening a second, disagreeing view.
   ══════════════════════════════════════════════════════════════════════════ */
let CORPUS=[], CUR=0, AUTO=null, DRAGGING=false;
const DWELL=6000;                       // ms a slide holds before the next one
// REDUCED comes from common.js — both files are plain <script>s sharing ONE
// global scope, so re-declaring a const here is a parse error that kills this
// whole file silently.
const ytThumb=(yid,size)=>`https://img.youtube.com/vi/${encodeURIComponent(yid)}/${size}.jpg`;

/* ---------- slider ---------- */
function renderSlides(){
  const track=$("#sliderTrack");
  track.innerHTML=CORPUS.map((v,i)=>{
    const yid=ytOf(v);
    // The first two stills are worth fetching eagerly; the rest are at most one
    // drag away, and lazy keeps ten images off the critical path of a page whose
    // job is to answer a question.
    return `<button type="button" class="cslide" data-i="${i}" tabindex="${i===0?0:-1}"
              aria-label="Play “${esc(v.title||v.id)}”">
      <img ${i<2?"":'loading="lazy"'} alt="" src="${yid?esc(ytThumb(yid,"maxresdefault")):""}"
        data-fallback="${yid?esc(ytThumb(yid,"hqdefault")):""}">
      <span class="cslide-veil" aria-hidden="true"></span>
      <span class="cslide-play" aria-hidden="true"><span>▶</span></span>
    </button>`;
  }).join("");
  // maxres doesn't exist for every video; swap to hq once, never in a loop.
  track.querySelectorAll("img").forEach(img=>{
    img.onerror=()=>{ img.onerror=null; if(img.dataset.fallback) img.src=img.dataset.fallback; };
  });
  track.querySelectorAll(".cslide").forEach(el=>el.addEventListener("click",()=>{
    if(DRAGGING) return;               // a drag that ends on a slide isn't a click
    const v=CORPUS[+el.dataset.i];
    if(v) openMoment([wholeVideo(v)], 0);
  }));
  $("#sliderDots").innerHTML=CORPUS.map((v,i)=>
    `<button type="button" class="cdot" role="tab" data-i="${i}"
       aria-label="Show ${esc(v.title||v.id)}" aria-current="${i===0}"></button>`).join("");
  $("#sliderDots").querySelectorAll(".cdot").forEach(d=>
    d.addEventListener("click",()=>{ go(+d.dataset.i); pause(); }));
  const many=CORPUS.length>1;
  $("#sliderPrev").hidden=!many; $("#sliderNext").hidden=!many;
  $("#sliderCount").hidden=!many; $("#sliderProgress").hidden=!many||REDUCED;
}

/* The caption belongs to the slide: which of the ten you're looking at, its
   state, and who made it. The HEADLINE deliberately doesn't move with it — see
   the note in demo.html. */
function paintCaption(v){
  $("#heroName").textContent=v.title||v.id;
  const b=statusBadge(v);
  $("#heroSub").innerHTML = v.status==="indexed"
    ? `${esc(b.label)}`
    : `<span class="${b.c}">${b.icon} ${esc(b.label)}</span>`;
  try{ renderSource(v); }catch(e){ console.warn("source line unavailable:",e); }
}

function go(i,{animate=true}={}){
  if(!CORPUS.length) return;
  CUR=(i%CORPUS.length+CORPUS.length)%CORPUS.length;
  const track=$("#sliderTrack");
  track.classList.toggle("anim",animate&&!REDUCED);
  track.style.transform=`translateX(${-CUR*100}%)`;
  $("#sliderCount").textContent=`${CUR+1} / ${CORPUS.length}`;
  $("#sliderDots").querySelectorAll(".cdot").forEach((d,n)=>
    d.setAttribute("aria-current",String(n===CUR)));
  // Only the visible slide is tabbable — ten buttons in the tab order for one
  // visible picture is its own kind of trap.
  track.querySelectorAll(".cslide").forEach((el,n)=>{ el.tabIndex = n===CUR?0:-1; });
  $("#vidList").querySelectorAll(".v-row").forEach(r=>
    r.setAttribute("aria-current",String(+r.dataset.i===CUR)));
  paintCaption(CORPUS[CUR]);
  restartProgress();
}

/* Auto-advance. Off entirely under prefers-reduced-motion — a thing that moves
   on its own is exactly what that setting asks us not to build. */
function play(){
  if(REDUCED||AUTO||CORPUS.length<2) return;
  AUTO=setInterval(()=>{ if(!document.hidden) go(CUR+1); },DWELL);
  restartProgress();
}
function pause(){
  if(AUTO){ clearInterval(AUTO); AUTO=null; }
  $("#sliderProgress").classList.remove("run");
}
function restartProgress(){
  const el=$("#sliderProgress");
  if(!AUTO||REDUCED){ el.classList.remove("run"); return; }
  el.style.setProperty("--dwell",DWELL+"ms");
  el.classList.remove("run"); void el.offsetWidth;   // restart the fill
  el.classList.add("run");
}

function wireSlider(){
  $("#sliderPrev").addEventListener("click",()=>{ go(CUR-1); pause(); });
  $("#sliderNext").addEventListener("click",()=>{ go(CUR+1); pause(); });
  const view=$("#sliderView");
  // Reading the caption shouldn't have the picture change underneath you; same
  // for tabbing into it.
  view.addEventListener("mouseenter",pause);
  view.addEventListener("mouseleave",play);
  view.addEventListener("focusin",pause);
  $("#heroSlider").addEventListener("keydown",e=>{
    if(e.key==="ArrowLeft"){ e.preventDefault(); go(CUR-1); pause(); }
    if(e.key==="ArrowRight"){ e.preventDefault(); go(CUR+1); pause(); }
  });

  /* Drag / swipe. Pointer events cover mouse, touch and pen in one path. The
     track follows the finger 1:1 while down — a carousel that only jumps on
     release feels broken on a touch screen — and settles on release: past a
     tenth of the frame, or a flick, moves one slide. */
  let x0=0, dx=0, w=0, down=false;
  const track=$("#sliderTrack");
  let captured=null;
  view.addEventListener("pointerdown",e=>{
    if(e.button!==undefined&&e.button!==0) return;
    // The arrows live INSIDE the viewport, so a press on one is a press on the
    // drag surface too. Leave those alone: capturing the pointer here retargets
    // the click at the viewport, and the arrow never hears about it.
    if(e.target.closest(".cslider-arrow")) return;
    down=true; DRAGGING=false; x0=e.clientX; dx=0; w=view.clientWidth||1;
    pause(); track.classList.remove("anim");
  });
  view.addEventListener("pointermove",e=>{
    if(!down) return;
    dx=e.clientX-x0;
    if(Math.abs(dx)>4){
      DRAGGING=true;
      // Capture only once it IS a drag, so a plain click stays a click: capture
      // makes every later event — including the click — target the viewport.
      if(captured===null){ try{ view.setPointerCapture(e.pointerId); captured=e.pointerId; }catch(_){} }
    }
    // Resist at the ends rather than pulling into empty space.
    const atEnd=(CUR===0&&dx>0)||(CUR===CORPUS.length-1&&dx<0);
    track.style.transform=`translateX(calc(${-CUR*100}% + ${(atEnd?dx*0.35:dx)}px))`;
  });
  const release=()=>{
    if(!down) return;
    down=false;
    if(captured!==null){ try{ view.releasePointerCapture(captured); }catch(_){} captured=null; }
    const moved=Math.abs(dx)>Math.max(40,w*0.1);
    go(moved ? (dx<0?CUR+1:CUR-1) : CUR);
    // Clear the flag AFTER this turn of the loop, so the click that follows a
    // drag is swallowed but the next real click is not.
    setTimeout(()=>{ DRAGGING=false; },0);
  };
  view.addEventListener("pointerup",release);
  view.addEventListener("pointercancel",release);
  // A background tab shouldn't burn through the corpus unseen.
  document.addEventListener("visibilitychange",()=>{ document.hidden ? pause() : play(); });
}

/* ---------- strip ---------- */
function renderStrip(){
  const n=CORPUS.length;
  if(!n) return;
  $("#cstrip-thumbs").innerHTML=CORPUS.slice(0,5).map(v=>{
    const yid=ytOf(v);
    return `<img loading="lazy" alt="" src="${yid?esc(ytThumb(yid,"default")):""}"
      onerror="this.style.visibility='hidden'">`;
  }).join("") + (n>5?`<span class="cstrip-more">+${n-5}</span>`:"");
  const indexed=CORPUS.filter(v=>v.status==="indexed").length;
  $("#cstrip-count").textContent = indexed===n
    ? `${n} videos, all indexed`
    : `${indexed} of ${n} videos indexed`;
  $("#cstrip-open").textContent=`Browse all ${n} →`;
  $("#vidDrawer-sub").textContent=`${n} videos, searched together. Click one to bring it into the frame.`;
  $("#corpus-strip").hidden=false;
}

/* ---------- drawer ---------- */
function renderList(){
  const q=($("#vidFilter").value||"").toLowerCase().trim();
  const rows=CORPUS.map((v,i)=>({v,i}))
    .filter(({v})=>!q||`${v.title||""} ${v.author||""}`.toLowerCase().includes(q));
  if(!rows.length){
    $("#vidList").innerHTML=`<p class="px-2 py-6 text-[13px] text-muted">No video matches that.</p>`;
    return;
  }
  $("#vidList").innerHTML=rows.map(({v,i})=>{
    const yid=ytOf(v), b=statusBadge(v);
    return `<button type="button" class="v-row" role="listitem" data-i="${i}"
      aria-current="${i===CUR}" aria-label="Show “${esc(v.title||v.id)}”">
      <img loading="lazy" alt="" src="${yid?esc(ytThumb(yid,"mqdefault")):""}"
        onerror="this.style.visibility='hidden'">
      <span class="min-w-0 flex-1" aria-hidden="true">
        <span class="block text-[12.5px] font-semibold text-ink leading-snug line-clamp-2">${esc(v.title||v.id)}</span>
        ${v.author?`<span class="block text-[11.5px] text-muted leading-snug mt-0.5">${esc(v.author)}</span>`:""}
        <span class="block text-[10.5px] ${v.status==="indexed"?"text-muted":esc(b.c)} mt-1">${esc(b.label)}</span>
      </span>
    </button>`;
  }).join("");
  $("#vidList").querySelectorAll(".v-row").forEach(el=>el.addEventListener("click",()=>{
    // Move the hero to it and close, rather than opening the player from in
    // here: the drawer and the player modal are both modal, and stacking them
    // nests two focus traps.
    go(+el.dataset.i); pause(); closeDrawer();
  }));
}

let _drawerTrigger=null;
const _drawerBg=()=>[...document.body.children].filter(el=>el.id!=="vidDrawer"&&el.tagName!=="SCRIPT");
function openDrawer(){
  if(!CORPUS.length) return;
  _drawerTrigger=document.activeElement;
  renderList();
  $("#vidDrawer").hidden=false;
  document.body.style.overflow="hidden";
  _drawerBg().forEach(el=>el.setAttribute("inert",""));
  $("#vidFilter").focus();
  document.addEventListener("keydown",trapDrawerTab);
}
function closeDrawer(){
  if($("#vidDrawer").hidden) return;
  $("#vidDrawer").hidden=true;
  document.body.style.overflow="";
  _drawerBg().forEach(el=>el.removeAttribute("inert"));
  document.removeEventListener("keydown",trapDrawerTab);
  if(_drawerTrigger&&_drawerTrigger.focus) _drawerTrigger.focus();
  _drawerTrigger=null;
}
function trapDrawerTab(e){
  if(e.key==="Escape"){ closeDrawer(); return; }
  if(e.key!=="Tab") return;
  const f=[...$("#vidDrawer").querySelectorAll('button,a[href],input,[tabindex]:not([tabindex="-1"])')]
    .filter(el=>!el.disabled&&el.offsetParent!==null);
  if(!f.length) return;
  const first=f[0], last=f[f.length-1];
  if(!$("#vidDrawer").contains(document.activeElement)){ e.preventDefault(); first.focus(); return; }
  if(e.shiftKey&&document.activeElement===first){ e.preventDefault(); last.focus(); }
  else if(!e.shiftKey&&document.activeElement===last){ e.preventDefault(); first.focus(); }
}
$("#cstrip-open").addEventListener("click",openDrawer);
$("#vidDrawer-close").addEventListener("click",closeDrawer);
$("#vidDrawer").querySelector("[data-drawer-close]").addEventListener("click",closeDrawer);
$("#vidFilter").addEventListener("input",renderList);

/* ---------- load ---------- */
(async function loadCorpus(){
  // Only the request itself may claim the API didn't answer. Wrapping the whole
  // function in one catch meant ANY later slip — a helper missing from a cached
  // common.js — printed "The API didn't answer" over a hero that had already
  // loaded fine. An error message that names the wrong culprit is worse than none.
  let videos;
  try{
    ({videos}=await apiJSON("/api/videos"));
  }catch(e){
    writeTitle("the sample");
    $("#heroName").textContent="Couldn't load the corpus";
    $("#heroSub").textContent="The API didn't answer.";
    return;
  }

  const shown=(videos||[]).filter(v=>v.is_sample);
  SAMPLE_IDS=shown.filter(v=>v.status==="indexed").map(v=>v.id);
  if(!shown.length){
    writeTitle("the sample");
    $("#heroName").textContent="The corpus is still loading";
    $("#heroSub").textContent="It restores itself on first start.";
    return;
  }

  // The featured video leads: the headline names it, so it has to be the first
  // slide too, or the two contradict each other for the first six seconds.
  // src/samples.py decides which one that is.
  const fi=shown.findIndex(v=>v.is_featured);
  CORPUS = fi>0 ? [shown[fi],...shown.slice(0,fi),...shown.slice(fi+1)] : shown;

  // The headline names the CORPUS, not one video in it. Ten videos are on the
  // slider and a question reaches across all of them, so naming a single title
  // undersold it — and it contradicted the frame the moment the slider moved on.
  // One creator for all ten means their name IS the corpus; a mixed corpus falls
  // back to counting.
  const authors=[...new Set(CORPUS.map(v=>v.author).filter(Boolean))];
  if(authors.length===1){
    SAMPLE_NAME=`${authors[0]} videos`;      // what the ask box and errors call it
    writeTitle(authors[0], "videos");        // …“3Blue1Brown” videos
  }else{
    SAMPLE_NAME = CORPUS.length>1 ? `these ${CORPUS.length} videos`
                : shortName(CORPUS[0].title||CORPUS[0].id);
    writeTitle(SAMPLE_NAME);
  }

  renderSlides();
  renderStrip();
  go(0,{animate:false});
  wireSlider();
  play();
})();

/* ---------- ask ---------- */
const qEl=$("#q");
function autogrow(){ qEl.style.height="auto"; qEl.style.height=Math.min(qEl.scrollHeight,260)+"px"; }
qEl.addEventListener("input",()=>{ autogrow(); ghostSync(); });
qEl.addEventListener("focus",ghostSync);
qEl.addEventListener("blur",ghostSync);
qEl.addEventListener("keydown",e=>{ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); ask(); }});
document.addEventListener("keydown",e=>{
  const tag=(document.activeElement||{}).tagName||"";
  if(e.key==="/" && document.activeElement!==qEl && !/^(INPUT|TEXTAREA)$/.test(tag)){
    e.preventDefault(); qEl.focus();
  }
});
$("#go").onclick=ask;
autogrow();

/* ---------- the research trace ----------
   The five stages the server actually reports, in the order it reports them.
   All five are drawn up front and dimmed, so the pipeline is legible BEFORE
   you ask — the pills are a diagram first and a progress bar second. */
const TRACE=[
  {id:"embedding", label:"Reading your question"},
  {id:"searching", label:"Searching screen + speech"},
  {id:"ranking",   label:"Ranking the moments"},
  {id:"parts",     label:"Searching each part"},
  {id:"reading",   label:"Opening those moments"},
  {id:"answering", label:"Writing the answer"},
];
const TRACE_BLURB={
  embedding: "Your question is embedded into the same space as the video's frames.",
  searching: "Two branches run at once: CLIP over the frames, and the transcript.",
  ranking:   "Ranked by RRF, merged into moments by timestamp, then re-judged by a cross-encoder.",
  parts:     "A multi-part question is split and every part is searched in parallel. Single-part questions skip this.",
  reading:   "The winning frames, their transcript excerpts and the speech around them are pulled for the model.",
  answering: "A vision model writes the answer from those moments, and cites them.",
};
let STAGE_INFO={};

function resetTrace(){
  STAGE_INFO={};
  $("#trace").innerHTML=TRACE.map(m=>
    `<span class="trace-pill" data-id="${m.id}"><span>${esc(m.label)}</span></span>`).join("");
  $$("#trace .trace-pill").forEach(el=>el.onclick=openTraceDetail);
  if(!$("#trace-detail").classList.contains("hidden")) renderTraceDetail();
}
function trace(stage, status, detail, ms){
  const el=$(`#trace .trace-pill[data-id="${stage}"]`);
  if(el){ el.classList.remove("run","done"); el.classList.add(status==="running"?"run":"done"); }
  const cur=STAGE_INFO[stage]||{};
  STAGE_INFO[stage]={status, detail:detail||cur.detail, ms:(ms!=null?ms:cur.ms)};
  if(!$("#trace-detail").classList.contains("hidden")) renderTraceDetail();
}
/* Everything that finished stays "done" — a stage the server never reached
   keeps its dim default, which is the honest reading of "didn't happen". */
function traceDone(){
  $$("#trace .trace-pill.run").forEach(el=>{ el.classList.remove("run"); el.classList.add("done"); });
  Object.keys(STAGE_INFO).forEach(k=>{ if(STAGE_INFO[k].status==="running") STAGE_INFO[k].status="done"; });
  if(!$("#trace-detail").classList.contains("hidden")) renderTraceDetail();
}
function renderTraceDetail(){
  const fmtMs=ms=>ms==null?"":(ms>=1000?(ms/1000).toFixed(1)+"s":Math.round(ms)+"ms");
  const rows=TRACE.filter(m=>STAGE_INFO[m.id]);
  $("#trace-detail").innerHTML = rows.length ? rows.map(m=>{
    const info=STAGE_INFO[m.id];
    return `<div>
      <div class="flex items-center gap-2 mb-1">
        <span class="w-1.5 h-1.5 rounded-full ${info.status==="done"?"bg-[#1f7a43]":"bg-coral"}"></span>
        <span class="text-[13px] font-semibold">${esc(m.label)}</span>
        ${info.ms!=null?`<span class="text-[11px] text-muted ml-auto tabular-nums">${fmtMs(info.ms)}</span>`:""}
      </div>
      <div class="pl-3.5 text-[13px] text-muted">${esc(info.detail||TRACE_BLURB[m.id]||"")}</div>
    </div>`;
  }).join("") : `<span class="text-muted text-[13px]">No details yet.</span>`;
}
function openTraceDetail(){
  $("#trace-detail").classList.remove("hidden");
  $("#trace-chev").textContent="Hide details ▾";
  renderTraceDetail();
}
$("#trace-toggle").onclick=()=>{
  const hidden=$("#trace-detail").classList.toggle("hidden");
  $("#trace-chev").textContent = hidden ? "Show details ▸" : "Hide details ▾";
  if(!hidden) renderTraceDetail();
};

/* Ask over SSE so the trace is live. fetch + a stream reader rather than
   EventSource, because the question travels in a POST body. Falls back to plain
   /api/ask if streaming is unavailable — the answer matters more than the
   progress display, which must never be what breaks asking. */
async function askStreaming(q, signal){
  const body=JSON.stringify({question:q, video_ids:SAMPLE_IDS});
  let r=null;
  try{
    r=await fetch("/api/ask_stream",{method:"POST",
      headers:{"Content-Type":"application/json"}, body, signal});
  }catch(e){ if(e.name==="AbortError") throw e; r=null; }
  if(!r || !r.ok || !r.body){
    return apiJSON("/api/ask",{method:"POST",
      headers:{"Content-Type":"application/json"}, body, signal});
  }
  const reader=r.body.getReader(), dec=new TextDecoder();
  let buf="", started=0, last=null, out=null, err=null;
  for(;;){
    const {value,done}=await reader.read();
    if(done) break;
    buf+=dec.decode(value,{stream:true});
    const frames=buf.split("\n\n"); buf=frames.pop();
    for(const f of frames){
      const line=f.split("\n").find(l=>l.startsWith("data:"));
      if(!line) continue;
      let ev; try{ ev=JSON.parse(line.slice(5).trim()); }catch{ continue; }
      if(ev.type==="stage"){
        const now=performance.now();
        // The elapsed time belongs to the stage that just ENDED — the server
        // reports a stage as it begins, so its duration isn't known until the
        // next one arrives.
        if(last) trace(last,"done",null,now-started);
        started=now; last=ev.stage;
        trace(ev.stage,"running",ev.detail||"");
      }else if(ev.type==="done"){
        if(last) trace(last,"done",null,performance.now()-started);
        out=ev.result;
      }else if(ev.type==="error"){ err=ev.detail||"The server couldn't answer that."; }
    }
  }
  if(err) throw new Error(err);
  if(!out) throw new Error("The answer stream ended before an answer arrived.");
  return out;
}

let _ctl=null;
async function ask(){
  const q=qEl.value.trim(); if(!q) return;
  if(_ctl) _ctl.abort();
  const ctl=_ctl=new AbortController();
  $("#results").classList.remove("hidden");
  $("#trace-widget").classList.remove("hidden");
  resetTrace();
  $("#article").innerHTML=`<div class="space-y-4">
    <div class="shimmer h-9 w-3/4 rounded-lg"></div>
    <div class="shimmer h-5 w-full rounded"></div>
    <div class="shimmer h-5 w-5/6 rounded"></div></div>`;
  $("#sources-wrap").classList.remove("hidden");
  $("#sources").innerHTML=skeletons(3);
  $("#go").disabled=true; $("#go").textContent="Thinking…"; $("#go").classList.add("busy");
  $("#results").scrollIntoView({behavior: REDUCED?"auto":"smooth", block:"start"});
  try{
    const r=await askStreaming(q, ctl.signal);
    traceDone();
    CITES=r.citations||[];
    let html="";
    if(r.answer) html+=`<div class="prose-body fade-in">${renderMarkdown(r.answer)}</div>`;
    if(r.parts && r.parts.length>1 && typeof partsLine==="function") html+=partsLine(r.parts);
    if(r.note) html+=`<p class="text-xs text-muted mt-3">${esc(r.note)}</p>`;
    if(!r.answer && !CITES.length) html=`<p class="text-muted">No matching moment in ${esc(SAMPLE_NAME)}. Try another question.</p>`;
    $("#article").innerHTML=html;
    if(CITES.length){
      $("#sources-wrap").classList.remove("hidden");
      $("#sources").innerHTML=CITES.map((c,i)=>momentCard(c,i)).join("");
      $$("#sources .source").forEach(el=>el.onclick=()=>openMoment(CITES,+el.dataset.n));
    } else {
      $("#sources-wrap").classList.add("hidden"); $("#sources").innerHTML="";
    }
  }catch(e){
    if(e.name==="AbortError") return;
    traceDone();
    $("#article").innerHTML=`<p class="text-coral2">Error: ${esc(e.message)}</p>`;
    $("#sources-wrap").classList.add("hidden"); $("#sources").innerHTML="";
  }finally{
    if(_ctl===ctl){ $("#go").disabled=false; $("#go").textContent="Ask →"; $("#go").classList.remove("busy"); }
  }
}
function skeletons(n){
  return Array.from({length:n},()=>`
    <div class="bg-card border border-line rounded-2xl overflow-hidden">
      <div class="shimmer aspect-video"></div>
      <div class="p-3 space-y-2"><div class="shimmer h-3 w-1/3 rounded"></div><div class="shimmer h-3 w-4/5 rounded"></div></div>
    </div>`).join("");
}
/* [n] pills in the answer open their moment */
$("#article").addEventListener("click",e=>{
  const c=e.target.closest(".cite"); if(c) openMoment(CITES,+c.dataset.n);
});

/* ---------- typewriter placeholder + example chips ---------- */
const EXAMPLES=["what is a large language model","how an LLM predicts the next word",
  "an animation of a neural network","training a model on huge amounts of text"];
function ghostIdle(){ return !qEl.value && document.activeElement!==qEl; }
function ghostSync(){ $("#ghost").classList.toggle("hide", !ghostIdle()); }
(function typewriter(){
  const ghost=$("#ghost");
  if(REDUCED){ ghost.textContent="Ask about anything shown on screen…"; return; }
  const caret='<span class="caret"></span>';
  let i=0, ch=0, dir=1;
  (function tick(){
    if(!ghostIdle()) return setTimeout(tick,400);
    const full=EXAMPLES[i%EXAMPLES.length];
    ch+=dir;
    ghost.innerHTML=esc(full.slice(0,Math.max(ch,0)))+caret;
    if(dir>0&&ch>=full.length){ dir=-1; return setTimeout(tick,2000); }
    if(dir<0&&ch<=0){ dir=1; i++; return setTimeout(tick,320); }
    setTimeout(tick, dir>0?46:22);
  })();
})();

$("#examples").innerHTML=EXAMPLES.map(q=>`<span class="chip">${esc(q)}</span>`).join("");
$$("#examples .chip").forEach(c=>c.onclick=()=>{
  qEl.value=c.textContent; autogrow(); ghostSync(); ask();
});
