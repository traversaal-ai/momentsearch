/* /app — the signed-in workspace: sessions, the playground chat, and the videos
 * each session holds.
 *
 * A session is one folder: its own videos + its own chat. Asking is scoped to
 * that session's indexed videos (the server does the scoping — see
 * src/api/sessions.py), so an empty session says "add a video" instead of
 * quietly answering from the demo corpus.
 */
if(!SIGNED()) location.replace("/signin");

let SESSIONS=[], CUR=null;            // CUR = the open session (with videos+messages)
let POLL=null, MAXMB=2048;
let SELECTED=new Set();               // which indexed videos a question searches (checkboxes)

wireModal();
$("#avatar").textContent=(SESSION.email||"?").trim()[0].toUpperCase();
$("#whoEmail").textContent=SESSION.email;
$("#signOutBtn").onclick=signOut;

/* Panes: three columns on xl, one at a time below that. */
$$(".paneTab").forEach(t=>t.onclick=()=>{
  $$(".paneTab").forEach(x=>x.classList.remove("active"));
  t.classList.add("active");
  document.body.dataset.pane=t.dataset.pane;
});
function showPane(name){
  const t=$(`.paneTab[data-pane="${name}"]`);
  if(t) t.click();
}

/* ---------- which model is answering ---------- */
(async function loadConfig(){
  try{
    const c=await apiJSON("/api/config"); const b=$("#llmBadge");
    MAXMB=c.max_upload_mb||MAXMB;
    $("#upLimit").textContent=`video files up to ${MAXMB} MB`;
    if(c.llm_configured){
      b.textContent="LLM: "+(c.llm_model||c.llm_provider);
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#cfe8d6] text-[#1f7a43] hidden sm:inline";
    } else {
      b.textContent="No LLM — moments only";
      b.className="ml-auto text-xs px-3 py-1 rounded-full border border-[#e7c46a] text-[#8a6d1a] bg-[#fbf2d8] hidden sm:inline";
    }
  }catch{ $("#llmBadge").textContent="API offline"; }
})();

/* ---------- sessions ---------- */
async function loadSessions(openId){
  try{
    const d=await apiJSON("/api/sessions");   // seeds "Demo videos" on a fresh workspace
    SESSIONS=d.sessions||[];
  }catch(e){
    if(/session/i.test(e.message)){ saveSession(null); return location.replace("/signin"); }
    throw e;
  }
  renderSessions();
  const want = openId || (CUR && SESSIONS.some(s=>s.id===CUR.id) ? CUR.id : null)
            || (SESSIONS[0] && SESSIONS[0].id);
  if(want) await openSession(want);
}

function renderSessions(){
  $("#sessionList").innerHTML=SESSIONS.map(s=>{
    const on = CUR && s.id===CUR.id;
    const bits=[`${s.video_count} video${s.video_count==1?"":"s"}`];
    if(s.message_count) bits.push(`${Math.floor(s.message_count/2)} question${s.message_count>2?"s":""}`);
    return `<button class="sessBtn w-full text-left rounded-xl px-3 py-2.5 border transition ${
      on ? "bg-card border-coral" : "bg-transparent border-transparent hover:bg-card hover:border-line"
    }" data-id="${esc(s.id)}">
      <div class="flex items-center gap-1.5">
        <span class="text-[13px] font-600 truncate">${esc(s.title)}</span>
        ${s.kind==="demo" ? '<span class="text-[9px] px-1 rounded bg-coral/15 text-coral2 shrink-0">demo</span>' : ''}
      </div>
      <div class="text-[11px] text-muted mt-0.5">${bits.join(" · ")}</div>
    </button>`;
  }).join("");
  $$("#sessionList .sessBtn").forEach(b=>b.onclick=()=>{ openSession(b.dataset.id); showPane("chat"); });
}

async function openSession(id){
  CUR=await apiJSON("/api/sessions/"+encodeURIComponent(id));
  renderSessions();
  $("#sessTitle").textContent=CUR.title;
  // Opening a session starts with every ready video checked (search everything);
  // the user unchecks the ones they want left out of a question.
  SELECTED=new Set(CUR.videos.filter(v=>v.status==="indexed").map(v=>v.id));
  $("#vidNote").textContent = CUR.kind==="demo"
    ? "Shared sample talks — searchable, not deletable."
    : "Uploads and links you add here stay in this session.";
  renderChat();
  renderSessionVideos();
  updateSessMeta();
  schedulePoll();
}

/* The playground subtitle reflects the CHECKED scope, not just the count. */
function updateSessMeta(){
  const ready=CUR.videos.filter(v=>v.status==="indexed");
  const n=ready.length;
  if(!n){ $("#sessMeta").textContent = CUR.videos.length ? "indexing…" : "no videos yet"; return; }
  const sel=ready.filter(v=>SELECTED.has(v.id)).length;
  $("#sessMeta").textContent = sel===0
    ? `${n} video${n==1?"":"s"} · none checked — check some to search`
    : sel===n
      ? `${n} video${n==1?"":"s"} · asking searches all of them`
      : `${sel} of ${n} checked · asking searches only the checked ones`;
}

$("#newSession").onclick=async()=>{
  const title=prompt("Name this session:", "New session");
  if(title===null) return;
  const s=await apiJSON("/api/sessions",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({title:title.trim()||"New session"})});
  await loadSessions(s.id);
  showPane("videos");            // it's empty — the next step is adding a video
};

$("#renameSession").onclick=async()=>{
  if(!CUR) return;
  const title=prompt("Rename session:", CUR.title);
  if(!title || !title.trim()) return;
  await apiJSON("/api/sessions/"+encodeURIComponent(CUR.id),{method:"PATCH",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify({title:title.trim()})});
  await loadSessions(CUR.id);
};

$("#deleteSession").onclick=async()=>{
  if(!CUR) return;
  if(!confirm(`Delete “${CUR.title}”?\n\nThis deletes the session, its chat, and its videos — their frames, transcript and search index — unless a video is also in another session, which keeps it.`)) return;
  await apiJSON("/api/sessions/"+encodeURIComponent(CUR.id),{method:"DELETE"});
  CUR=null;
  await loadSessions();
};

/* ---------- the chat ---------- */
function renderChat(){
  const box=$("#chat");
  if(!CUR.messages.length){
    box.innerHTML=`
      <div class="max-w-2xl mx-auto text-center py-10">
        <div class="display text-2xl font-600">${CUR.videos.length ? "Ask this session anything" : "This session is empty"}</div>
        <p class="text-sm text-muted mt-2 leading-relaxed">${CUR.videos.length
          ? "Questions are matched against what's on screen <b>and</b> what's said. Click any citation to watch that exact moment."
          : "Add a YouTube link or upload a video on the right, and it becomes searchable here once it's indexed."}</p>
        ${CUR.videos.length ? `<div id="starters" class="flex flex-wrap gap-2 justify-center mt-5"></div>` : ""}
      </div>`;
    if(CUR.videos.length){
      const S=["What is this about?","Show me a diagram","What's on the busiest slide?"];
      $("#starters").innerHTML=S.map(s=>`<span class="chip">${esc(s)}</span>`).join("");
      $$("#starters .chip").forEach(c=>c.onclick=()=>{ $("#q").value=c.textContent; ghostSync(); send(); });
    }
    return;
  }
  box.innerHTML=CUR.messages.map(m=>m.role==="user" ? bubble(m) : answer(m)).join("");
  wireChatClicks();
  box.scrollTop=box.scrollHeight;
}
function bubble(m){
  return `<div class="flex justify-end">
    <div class="bg-coral/10 border border-coral/25 rounded-2xl rounded-br-md px-4 py-2.5 max-w-[85%] sm:max-w-[70%]">
      <div class="text-[14.5px] leading-relaxed">${esc(m.content)}</div>
    </div></div>`;
}
function answer(m){
  const cites=m.citations||[];
  const note=(m.meta||{}).note;
  return `<div class="max-w-3xl" data-msg="${m.id}">
    <div class="prose-body text-[15px]">${renderMarkdown(m.content)}</div>
    ${note?`<p class="text-[11px] text-muted mt-2">${esc(note)}</p>`:""}
    ${cites.length?`
      <div class="text-[11px] uppercase tracking-wider text-muted font-semibold mt-4 mb-2">Moments</div>
      <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        ${cites.map((c,i)=>momentCard(c,i)).join("")}
      </div>`:""}
  </div>`;
}
/* Cards and [n] pills both open the modal, using the citations of THEIR turn. */
function wireChatClicks(){
  $$("#chat [data-msg]").forEach(wrap=>{
    const m=CUR.messages.find(x=>String(x.id)===wrap.dataset.msg);
    const cites=(m&&m.citations)||[];
    wrap.querySelectorAll(".source").forEach(el=>
      el.onclick=()=>openMoment(cites,+el.dataset.n));
    wrap.querySelectorAll(".cite").forEach(el=>
      el.onclick=()=>openMoment(cites,+el.dataset.n));
  });
}

const qEl=$("#q");
function autogrow(){ qEl.style.height="auto"; qEl.style.height=Math.min(qEl.scrollHeight,150)+"px"; }
qEl.addEventListener("input",()=>{ autogrow(); ghostSync(); });
qEl.addEventListener("focus",ghostSync);
qEl.addEventListener("blur",ghostSync);
qEl.addEventListener("keydown",e=>{ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); send(); }});
$("#go").onclick=send;

let _sending=false;
async function send(){
  const q=qEl.value.trim();
  if(!q || !CUR || _sending) return;
  _sending=true;
  qEl.value=""; autogrow(); ghostSync();
  $("#go").disabled=true; $("#go").classList.add("busy");
  // Show the question and a placeholder immediately; the server persists both
  // sides of the turn and returns the stored assistant message.
  CUR.messages.push({id:"tmp-u", role:"user", content:q, citations:[], meta:{}});
  renderChat();
  const box=$("#chat");
  box.insertAdjacentHTML("beforeend",
    `<div id="pending" class="max-w-3xl space-y-3">
       <div class="shimmer h-4 w-2/3 rounded"></div>
       <div class="shimmer h-4 w-full rounded"></div>
       <div class="shimmer h-4 w-4/6 rounded"></div>
     </div>`);
  box.scrollTop=box.scrollHeight;
  try{
    const d=await apiJSON(`/api/sessions/${encodeURIComponent(CUR.id)}/ask`,{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({question:q, video_ids:[...SELECTED]})});
    CUR.messages=CUR.messages.filter(m=>m.id!=="tmp-u");
    CUR.messages.push({id:"u-"+d.message.id, role:"user", content:q, citations:[], meta:{}});
    CUR.messages.push(d.message);
    renderChat();
    loadSessions(CUR.id);        // refresh the sidebar's counts / ordering
  }catch(e){
    const p=$("#pending");
    if(p) p.outerHTML=`<p class="text-coral2 text-sm">Error: ${esc(e.message)}</p>`;
  }finally{
    _sending=false;
    $("#go").disabled=false; $("#go").classList.remove("busy");
  }
}

/* ---------- videos in this session ---------- */
function renderSessionVideos(){
  const box=$("#sessVideos");
  if(!CUR.videos.length){
    box.innerHTML='<p class="text-[13px] text-muted">Nothing here yet. Add a link or drop a file above.</p>';
    return;
  }
  box.innerHTML=CUR.videos.map(v=>{
    const b=statusBadge(v);
    const busy=INFLIGHT.includes(v.status);
    const rail = busy
      ? (v.progress ? `<span class="rail" style="width:${Math.round(v.progress*100)}%"></span>`
                    : `<span class="rail indet"></span>`)
      : "";
    const yid=v.id.startsWith("yt_") ? v.id.slice(3) : null;
    const thumb=yid
      ? `<img loading="lazy" src="https://img.youtube.com/vi/${esc(yid)}/default.jpg" class="w-14 h-9 object-cover rounded-md border border-line shrink-0" onerror="this.style.opacity=0">`
      : `<div class="w-14 h-9 rounded-md border border-line bg-paper2 shrink-0"></div>`;
    // Checkbox = search scope. Only ready videos can be searched, so only they
    // get a live checkbox; anything still indexing shows a placeholder.
    const check = v.status==="indexed"
      ? `<input type="checkbox" class="vidsel accent-coral w-4 h-4 shrink-0 cursor-pointer" data-sel="${esc(v.id)}" ${SELECTED.has(v.id)?"checked":""} title="Include this video in the search (unchecking doesn't delete it)">`
      : `<span class="w-4 shrink-0" title="Searchable once indexed"></span>`;
    return `<div class="relative overflow-hidden bg-card border border-line rounded-xl p-2.5 flex gap-2.5 items-center">
      ${check}
      ${thumb}
      <div class="min-w-0 flex-1">
        <div class="text-[12.5px] font-600 leading-snug line-clamp-2">${esc(v.title||v.id)}</div>
        <div class="text-[11px] ${b.c} mt-0.5">${b.icon} ${esc(b.label)}</div>
      </div>
      <div class="flex flex-col gap-1 shrink-0">
        ${v.status==="failed"?`<button class="text-muted hover:text-coral2 text-xs" data-retry="${esc(v.id)}" title="Retry">↻</button>`:""}
        <button class="text-muted hover:text-coral2 text-xs" data-remove="${esc(v.id)}" title="Remove from this session (keeps the video in your workspace)">✕</button>
        ${v.is_sample?"":`<button class="text-muted hover:text-coral2 text-xs" data-del="${esc(v.id)}" data-title="${esc(v.title||v.id)}" title="Delete permanently — removes frames &amp; search vectors everywhere">🗑</button>`}
      </div>
      ${rail}
    </div>`;
  }).join("");

  box.querySelectorAll(".vidsel").forEach(c=>c.onchange=()=>{
    if(c.checked) SELECTED.add(c.dataset.sel); else SELECTED.delete(c.dataset.sel);
    updateSessMeta();
  });

  box.querySelectorAll("[data-remove]").forEach(b=>b.onclick=async()=>{
    await apiJSON(`/api/sessions/${encodeURIComponent(CUR.id)}/videos/${encodeURIComponent(b.dataset.remove)}`,
      {method:"DELETE"});
    await openSession(CUR.id); loadSessions(CUR.id);
  });
  box.querySelectorAll("[data-retry]").forEach(b=>b.onclick=async()=>{
    await api("/api/videos/"+encodeURIComponent(b.dataset.retry)+"/retry",{method:"POST"});
    await openSession(CUR.id);
  });
  box.querySelectorAll("[data-del]").forEach(b=>b.onclick=async()=>{
    if(!confirm(`Delete “${b.dataset.title}” permanently?\n\nThis removes its frames and search vectors from your whole workspace. This can’t be undone.`)) return;
    try{ await apiJSON("/api/videos/"+encodeURIComponent(b.dataset.del),{method:"DELETE"}); }
    catch(e){ alert(e.message); return; }
    await openSession(CUR.id); loadSessions(CUR.id);
  });
}

/* While anything is indexing, refresh this session so the rails move and a
   finished video becomes searchable without a manual reload. */
function schedulePoll(){
  if(POLL){ clearInterval(POLL); POLL=null; }
  if(!CUR || !CUR.videos.some(v=>INFLIGHT.includes(v.status))) return;
  POLL=setInterval(async()=>{
    if(!CUR) return;
    const before=CUR.videos.map(v=>v.status+v.progress).join();
    const wasIndexed=new Set(CUR.videos.filter(v=>v.status==="indexed").map(v=>v.id));
    const fresh=await apiJSON("/api/sessions/"+encodeURIComponent(CUR.id));
    CUR.videos=fresh.videos;
    // A video that just finished indexing joins the search checked by default.
    CUR.videos.forEach(v=>{ if(v.status==="indexed" && !wasIndexed.has(v.id)) SELECTED.add(v.id); });
    if(CUR.videos.map(v=>v.status+v.progress).join()!==before){
      renderSessionVideos();
      updateSessMeta();
      if(!CUR.messages.length) renderChat();   // empty-state copy depends on video count
    }
    if(!CUR.videos.some(v=>INFLIGHT.includes(v.status))){
      clearInterval(POLL); POLL=null; $("#ingestStatus").textContent="";
      loadSessions(CUR.id);
    }
  }, 2500);
}

/* ---------- adding videos (into THIS session) ---------- */
async function register(body){
  const st=$("#ingestStatus");
  try{
    await apiJSON("/api/videos",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(Object.assign({session_id:CUR.id}, body))});
    st.textContent="Queued — indexing in the background.";
    await openSession(CUR.id); loadSessions(CUR.id);
  }catch(e){ st.textContent="Error: "+e.message; }
}

$("#ytBtn").onclick=async()=>{
  const u=$("#ytUrl").value.trim(); if(!u || !CUR) return;
  $("#ytBtn").disabled=true; $("#ytUrl").value="";
  await register({url:u});
  $("#ytBtn").disabled=false;
};
$("#ytUrl").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); $("#ytBtn").click(); }});

$("#upFile").onchange=e=>{ uploadAll([...e.target.files]); e.target.value=""; };
const drop=$("#drop");
["dragenter","dragover"].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault(); drop.classList.add("border-coral"); }));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault(); drop.classList.remove("border-coral"); }));
drop.addEventListener("drop",e=>{
  const files=[...(e.dataTransfer?.files||[])].filter(f=>f.type.startsWith("video/"));
  if(files.length) uploadAll(files);
});

/* One row per file with real byte progress, then registered into this session. */
async function uploadAll(files){
  if(!CUR) return;
  for(const f of files){
    if(f.size > MAXMB*1024*1024){
      $("#ingestStatus").textContent=`“${f.name}” is over the ${MAXMB} MB limit.`;
      continue;
    }
    const id="up"+Math.random().toString(36).slice(2,8);
    $("#upList").insertAdjacentHTML("beforeend",
      `<div id="${id}" class="text-[11.5px]">
         <div class="flex gap-2"><span class="truncate flex-1">${esc(f.name)}</span><span class="tabular-nums" data-pct>0%</span></div>
         <div class="h-1 bg-paper2 rounded-full mt-1 overflow-hidden"><div class="h-full bg-coral rounded-full" style="width:0" data-bar></div></div>
       </div>`);
    const row=$("#"+id);
    try{
      // Hash small-enough files up front so an identical re-upload skips the
      // upload AND the re-embed (crypto.subtle needs the whole file in memory).
      const sha = f.size <= 300*1024*1024 ? await sha256Hex(f) : null;
      const p=await apiJSON("/api/videos/presign",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({filename:f.name, content_type:f.type||"video/mp4", size:f.size, sha256:sha})});
      if(p.mode==="exists"){
        // identical content already indexed — link the existing video, no upload
        await apiJSON(`/api/sessions/${encodeURIComponent(CUR.id)}/videos`,{method:"POST",
          headers:{"Content-Type":"application/json"}, body:JSON.stringify({video_id:p.video_id})});
        row.remove();
        $("#ingestStatus").textContent="Already indexed — added instantly.";
        await openSession(CUR.id); loadSessions(CUR.id);
        continue;
      }
      await putWithProgress(p.url, p.headers, f, pct=>{
        row.querySelector("[data-bar]").style.width=Math.round(pct*100)+"%";
        row.querySelector("[data-pct]").textContent=Math.round(pct*100)+"%";
      });
      await register({video_id:p.video_id, key:p.key, title:f.name.replace(/\.[^.]+$/,"")});
      row.remove();
    }catch(e){
      row.querySelector("[data-pct]").textContent="failed";
      row.querySelector("[data-pct]").className="text-coral2";
      $("#ingestStatus").textContent="Error: "+e.message;
    }
  }
}

/* ---------- placeholder ---------- */
function ghostIdle(){ return !qEl.value && document.activeElement!==qEl; }
function ghostSync(){ $("#ghost").classList.toggle("hide", !ghostIdle()); }

loadSessions();
