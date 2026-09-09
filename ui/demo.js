/* /demo — the open door: search the shared sample corpus.
 *
 * Read-only and stateless by design: no sessions, no uploads, nothing saved.
 * Anonymous callers are the default tenant, which OWNS the samples, so no
 * sign-in is needed to search them.
 *
 * The page is one screen: the video on the right of the hero, the ask box on
 * the left, and everything the server does to answer laid out as a trace of
 * pills. There is deliberately no recent-questions strip — one answer is on
 * screen at a time and asking again replaces it.
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
function writeTitle(name){
  const pen = `<span class="word" style="--i:1"><span class="mark text-coral"><span class="ink">hard question</span><i class="nib" aria-hidden="true"></i>${UNDERLINE}</span></span>`;
  $("#heroTitle").innerHTML =
    `<span class="word" style="--i:0">Ask a</span> ${pen}<br>`
    + `<span class="word block text-2xl sm:text-3xl mt-3 text-muted font-semibold leading-snug" style="--i:2">`
    + `about <span class="text-ink">“${esc(name)}”</span></span>`;
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

/* Nothing to play until /api/videos names the sample, so the still starts inert
   and the badge only appears once there is a video behind it. */
function noPlayback(){
  $("#heroPlay").disabled=true;
  $("#heroPlayBadge").classList.add("hidden");
}
noPlayback();

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

(async function loadSample(){
  const img=$("#heroThumb"), name=$("#heroName"), sub=$("#heroSub");

  // Only the request itself may claim the API didn't answer. Wrapping the whole
  // function in one catch meant ANY later slip — a helper missing from a cached
  // common.js — printed "The API didn't answer" over a hero that had already
  // loaded fine. An error message that names the wrong culprit is worse than none.
  let videos;
  try{
    ({videos}=await apiJSON("/api/videos"));
  }catch(e){
    writeTitle("the sample");
    name.textContent="Couldn't load the sample";
    sub.textContent="The API didn't answer.";
    return;
  }

  const shown=(videos||[]).filter(v=>v.is_sample);
  SAMPLE_IDS=shown.filter(v=>v.status==="indexed").map(v=>v.id);
  if(!shown.length){
    writeTitle("the sample");
    name.textContent="The sample is still indexing";
    sub.textContent="It seeds itself on first start. A few minutes, once.";
    return;
  }

  // Identity: the headline, the still and the caption.
  const v=shown[0], yid=ytOf(v);
  SAMPLE_NAME=shortName(v.title||v.id);
  writeTitle(SAMPLE_NAME);
  if(yid){
    img.src=`https://img.youtube.com/vi/${encodeURIComponent(yid)}/maxresdefault.jpg`;
    img.onerror=()=>{ img.onerror=null; img.src=`https://img.youtube.com/vi/${encodeURIComponent(yid)}/hqdefault.jpg`; };
  }
  img.alt=v.title||v.id;
  name.textContent=v.title||v.id;
  // Say what state it's in rather than implying it's ready: a sample that is
  // still embedding can't answer, and the badge is the only place that shows.
  const b=statusBadge(v);
  sub.innerHTML = v.status==="indexed"
    ? `${esc(b.label)}`
    : `<span class="${b.c}">${b.icon} ${esc(b.label)}</span>`;

  // Extras, fenced off: the source link and turning the still into a play button
  // are additions to a hero that is already correct without them, so they get
  // their own failure. The still becomes the same player modal a citation opens
  // — from 0:00, nothing matched — and playing needs no index, so it works even
  // while the sample is still embedding.
  try{
    renderSource(v);
    const play=$("#heroPlay");
    play.disabled=false;
    play.setAttribute("aria-label",`Play “${v.title||v.id}” from the start`);
    $("#heroPlayBadge").classList.remove("hidden");
    play.onclick=()=>openMoment([wholeVideo(v)], 0);
  }catch(e){
    console.warn("hero playback/source unavailable:", e);
    noPlayback();
  }
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
