/* /signin — trade an email for a workspace, then go to /app. */
const DEMO_EMAILS=["ada@demo.dev","kai@demo.dev","rui@demo.dev"];

$("#demoEmails").innerHTML=DEMO_EMAILS
  .map(e=>`<span class="chip" data-email="${esc(e)}">${esc(e)}</span>`).join("");
$$("#demoEmails [data-email]").forEach(el=>el.onclick=()=>signIn(el.dataset.email));

/* A stored token can be expired, or signed by a server that has restarted with
   a new key — verify before showing "you're signed in". */
(async function show(){
  if(!SIGNED()) return;
  try{
    const me=await apiJSON("/api/auth/me");
    saveSession({email:me.email, user_id:me.user_id, token:SESSION.token});
    $("#curEmail").textContent=me.email;
    $("#signedIn").classList.remove("hidden");
    $("#form").classList.add("hidden");
  }catch{ saveSession(null); }   // stale — fall through to the form
})();

$("#switch").onclick=()=>{
  saveSession(null);
  $("#signedIn").classList.add("hidden");
  $("#form").classList.remove("hidden");
  $("#authEmail").focus();
};

$("#authForm").onsubmit=e=>{ e.preventDefault(); signIn($("#authEmail").value); };

async function signIn(email){
  const err=$("#authError"), btn=$("#authGo");
  err.classList.add("hidden");
  btn.disabled=true; btn.textContent="Signing in…";
  try{
    // Not api(): signing in is how you GET a session, so there's none to send.
    const r=await fetch("/api/auth/demo",{method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({email})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(d.detail||"Sign-in failed.");
    saveSession({email:d.email, user_id:d.user_id, token:d.token});
    location.href="/app";
  }catch(e){
    err.textContent=e.message; err.classList.remove("hidden");
    btn.disabled=false; btn.textContent="Continue →";
  }
}
