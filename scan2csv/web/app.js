const $ = (id) => document.getElementById(id);

function tabTo(name) {
  document.querySelectorAll(".tab").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll("nav button").forEach((el) => el.classList.remove("active"));
  $(`tab-${name}`).classList.add("active");
  document.querySelector(`nav button[data-tab="${name}"]`).classList.add("active");
}

document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => tabTo(btn.dataset.tab));
});

let currentJob = null;
let es = null;

async function loadSettings() {
  const r = await fetch("/api/settings");
  const s = await r.json();
  $("llmUrl").value = s.llm?.base_url || "";
  $("llmKey").value = ""; // never echo full secret placeholders as real
  $("llmModel").value = s.llm?.model || "";
  $("apiyiEp").value = s.apiyi?.endpoint || "";
  $("apiyiKey").value = "";
  $("apiyiModel").value = s.apiyi?.model || "";
  $("apiyiSize").value = s.apiyi?.size || "";
}

$("btnSave").onclick = async () => {
  const body = {
    llm: {
      base_url: $("llmUrl").value.trim(),
      model: $("llmModel").value.trim(),
    },
    apiyi: {
      endpoint: $("apiyiEp").value.trim(),
      model: $("apiyiModel").value.trim(),
      size: $("apiyiSize").value.trim(),
    },
  };
  if ($("llmKey").value.trim()) body.llm.api_key = $("llmKey").value.trim();
  if ($("apiyiKey").value.trim()) body.apiyi.api_key = $("apiyiKey").value.trim();
  const r = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  $("saveMsg").textContent = r.ok ? "已保存到本机 jobs 旁 settings.json" : await r.text();
};

$("btnTestLlm").onclick = async () => {
  const body = {
    base_url: $("llmUrl").value.trim() || null,
    api_key: $("llmKey").value.trim() || null,
    model: $("llmModel").value.trim() || null,
  };
  const r = await fetch("/api/test-llm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const t = await r.json().catch(() => ({}));
  $("llmTestMsg").textContent = r.ok ? `OK: ${t.reply}` : `失败: ${t.detail || r.status}`;
};

$("btnModels").onclick = async () => {
  // save url/key first if typed
  if ($("llmKey").value.trim() || $("llmUrl").value.trim()) {
    await $("btnSave").onclick();
  }
  const r = await fetch("/api/models");
  const data = await r.json().catch(() => ({}));
  const sel = $("modelSelect");
  sel.innerHTML = "";
  (data.models || []).forEach((id) => {
    const o = document.createElement("option");
    o.value = id;
    o.textContent = id;
    sel.appendChild(o);
  });
  if (!r.ok) $("llmTestMsg").textContent = data.detail || "刷新模型失败";
};

$("modelSelect").onchange = () => {
  if ($("modelSelect").value) $("llmModel").value = $("modelSelect").value;
};

function pushTimeline(ev) {
  const li = document.createElement("li");
  li.className = ev.status || "";
  li.textContent = `${ev.ts || ""}  [${ev.step}] ${ev.status} — ${ev.message || ""}`;
  $("timeline").appendChild(li);
  $("timeline").scrollTop = $("timeline").scrollHeight;
  if (typeof ev.progress === "number") {
    $("bar").style.width = `${Math.round(ev.progress * 100)}%`;
  }
}

function artUrl(jobId, rel) {
  return `/api/jobs/${jobId}/artifacts/${rel}`;
}

async function refreshArtifacts(jobId) {
  $("imgSource").src = artUrl(jobId, "page-001/source.png") + `?t=${Date.now()}`;
  $("imgEnhanced").src = artUrl(jobId, "page-001/enhanced.png") + `?t=${Date.now()}`;
  $("imgOverlay").src = artUrl(jobId, "page-001/ocr_overlay.png") + `?t=${Date.now()}`;
  $("dlWide").href = artUrl(jobId, "page-001/final_wide.csv");
  $("dlLong").href = artUrl(jobId, "page-001/final_long.csv");
  $("dlSchema").href = artUrl(jobId, "page-001/page_schema.json");
  try {
    const r = await fetch(artUrl(jobId, "page-001/final_wide.csv"));
    if (r.ok) $("csvPreview").textContent = await r.text();
  } catch (_) {}
}

$("btnStart").onclick = async () => {
  const f = $("file").files?.[0];
  if (!f) {
    alert("请选择文件");
    return;
  }
  $("timeline").innerHTML = "";
  $("bar").style.width = "0%";
  $("csvPreview").textContent = "";
  const fd = new FormData();
  fd.append("file", f);
  fd.append("user_notes", $("notes").value || "");
  if ($("skipEnhance").checked) fd.append("skip_enhance", "1");
  if ($("skipLlm").checked) fd.append("skip_llm", "1");
  const r = await fetch("/api/jobs", { method: "POST", body: fd });
  const data = await r.json();
  if (!r.ok) {
    alert(data.detail || "创建任务失败");
    return;
  }
  currentJob = data.job_id;
  $("jobLabel").textContent = `任务 ${currentJob}`;
  if (es) es.close();
  es = new EventSource(`/api/jobs/${currentJob}/events`);
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    pushTimeline(ev);
    if (["enhance", "ocr", "export", "agent_structure", "job"].includes(ev.step)) {
      refreshArtifacts(currentJob);
    }
    if (ev.step === "job" && ["succeeded", "failed", "cancelled"].includes(ev.status)) {
      refreshArtifacts(currentJob);
      es.close();
    }
  };
};

$("btnRefreshJobs").onclick = async () => {
  const r = await fetch("/api/jobs");
  const data = await r.json();
  const ul = $("jobList");
  ul.innerHTML = "";
  (data.jobs || []).forEach((j) => {
    const li = document.createElement("li");
    li.innerHTML = `<code>${j.job_id}</code> — ${j.status}`;
    li.style.cursor = "pointer";
    li.onclick = () => {
      currentJob = j.job_id;
      $("jobLabel").textContent = `任务 ${currentJob}`;
      tabTo("work");
      refreshArtifacts(currentJob);
    };
    ul.appendChild(li);
  });
};

loadSettings().catch(console.error);
