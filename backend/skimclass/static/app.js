
const $ = (id) => document.getElementById(id);

let activeSessionId = null;
let quizSelections = {};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

function setSessionInfo(text) {
  $("session-info").textContent = text || "";
}

async function startSession() {
  const course = $("course-name").value.trim();
  if (!course) {
    alert("请先填写课程名称");
    return;
  }
  const mode = $("mode").value;
  const interval = Number($("interval").value) || 30;

  try {
    const data = await api("/api/session", {
      method: "POST",
      body: JSON.stringify({
        course_name: course,
        mode,
        interval_sec: interval,
      }),
    });
    activeSessionId = data.id;
    setSessionInfo(`当前 Session：${data.course_name} (${data.id.slice(0, 8)}...)`);
    $("segments").innerHTML = "";
    $("segments-empty").style.display = "block";
  } catch (err) {
    alert("创建 Session 失败：" + err.message);
  }
}

async function stopSession() {
  if (!activeSessionId) return;
  try {
    await api(`/api/session/${activeSessionId}/stop`, { method: "POST" });
    alert("已停止截屏采集。");
  } catch (err) {
    alert("停止失败：" + err.message);
  }
}

async function summarize() {
  if (!activeSessionId) {
    alert("请先启动一节课");
    return;
  }
  try {
    await api(`/api/session/${activeSessionId}/summarize`, { method: "POST" });
    alert("已提交生成 Outline 的任务，稍等几分钟后点击刷新。");
  } catch (err) {
    alert("提交失败：" + err.message);
  }
}

function renderSegments(list) {
  const container = $("segments");
  container.innerHTML = "";
  if (!list || list.length === 0) {
    $("segments-empty").style.display = "block";
    return;
  }
  $("segments-empty").style.display = "none";
  for (const seg of list) {
    const li = document.createElement("li");
    li.className = "segment-item";
    const h3 = document.createElement("h3");
    h3.textContent = `${seg.idx}. ${seg.title}`;
    li.appendChild(h3);

    const summaryDiv = document.createElement("div");
    summaryDiv.className = "segment-summary";
    summaryDiv.textContent = seg.summary;
    li.appendChild(summaryDiv);

    if (seg.open_questions && seg.open_questions.length > 0) {
      const oqDiv = document.createElement("div");
      oqDiv.className = "segment-open";
      oqDiv.textContent = "可能存在的困惑： " + seg.open_questions.join("； ");
      li.appendChild(oqDiv);
    }

    container.appendChild(li);
  }
}

async function refreshSegments() {
  if (!activeSessionId) {
    alert("请先启动一节课");
    return;
  }
  try {
    const list = await api(`/api/session/${activeSessionId}/segments`);
    renderSegments(list);
  } catch (err) {
    alert("刷新失败：" + err.message);
  }
}

async function ask() {
  if (!activeSessionId) {
    alert("请先启动一节课");
    return;
  }
  const q = $("qa-input").value.trim();
  if (!q) return;
  $("qa-answer").textContent = "思考中……";
  try {
    const data = await api(`/api/session/${activeSessionId}/qa`, {
      method: "POST",
      body: JSON.stringify({ question: q }),
    });
    $("qa-answer").textContent = data.answer;
  } catch (err) {
    $("qa-answer").textContent = "";
    alert("提问失败：" + err.message);
  }
}

function renderQuiz(items) {
  const listEl = $("quiz-list");
  listEl.innerHTML = "";
  quizSelections = {};
  if (!items || items.length === 0) {
    $("quiz-empty").style.display = "block";
    return;
  }
  $("quiz-empty").style.display = "none";

  items.forEach((item, idx) => {
    const li = document.createElement("li");
    li.className = "quiz-item";

    const qDiv = document.createElement("div");
    qDiv.className = "quiz-question";
    qDiv.textContent = `${idx + 1}. ${item.question}`;
    li.appendChild(qDiv);

    const ul = document.createElement("ul");
    ul.className = "quiz-options";

    item.options.forEach((opt, i) => {
      const optLi = document.createElement("li");
      optLi.className = "quiz-option";
      const label = String.fromCharCode(65 + i);
      optLi.textContent = `${label}. ${opt}`;

      optLi.onclick = () => {
        quizSelections[idx] = i;
        // 高亮
        const children = ul.children;
        for (let j = 0; j < children.length; j++) {
          children[j].classList.remove("correct", "incorrect");
        }
        if (i === item.correct_index) {
          optLi.classList.add("correct");
        } else {
          optLi.classList.add("incorrect");
          const correctLi = children[item.correct_index];
          if (correctLi) correctLi.classList.add("correct");
        }
        if (item.explanation) {
          explDiv.textContent = "解析：" + item.explanation;
        }
      };

      ul.appendChild(optLi);
    });

    li.appendChild(ul);

    const explDiv = document.createElement("div");
    explDiv.className = "quiz-expl";
    li.appendChild(explDiv);

    listEl.appendChild(li);
  });
}

async function generateQuiz() {
  if (!activeSessionId) {
    alert("请先启动一节课");
    return;
  }
  try {
    const data = await api(`/api/session/${activeSessionId}/quiz`, {
      method: "POST",
    });
    renderQuiz(data.items);
  } catch (err) {
    alert("生成小测失败：" + err.message);
  }
}

async function generateReport() {
  if (!activeSessionId) {
    alert("请先启动一节课");
    return;
  }
  $("report-text").textContent = "生成中……";
  try {
    const data = await api(`/api/session/${activeSessionId}/report`);
    $("report-text").textContent = data.report;
    $("report-empty").style.display = "none";
  } catch (err) {
    $("report-text").textContent = "";
    alert("生成失败：" + err.message);
  }
}

async function generateRecom() {
  if (!activeSessionId) {
    alert("请先启动一节课");
    return;
  }
  $("recom-text").textContent = "生成中……";
  try {
    const data = await api(`/api/session/${activeSessionId}/recommendations`);
    $("recom-text").textContent = data.text;
    $("recom-empty").style.display = "none";
  } catch (err) {
    $("recom-text").textContent = "";
    alert("生成失败：" + err.message);
  }
}

// settings
async function openSettings() {
  $("settings-modal").classList.remove("hidden");
  $("settings-hint").textContent = "";
  try {
    const data = await api("/api/settings");
    $("api-base").value = data.api_base || "";
    $("api-model").value = data.model || "";
    $("settings-hint").textContent = data.api_key_set
      ? "已配置密钥。再次保存时如留空 API Key 将保持不变。"
      : "尚未配置密钥，请至少填写一次。";
  } catch {
    // ignore
  }
}

async function saveSettings() {
  const base = $("api-base").value.trim();
  const model = $("api-model").value.trim();
  const key = $("api-key").value.trim();
  if (!base || !model) {
    alert("API Base 和 Model 不能为空");
    return;
  }
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ api_base: base, api_key: key, model }),
    });
    $("settings-hint").textContent = data.api_key_set
      ? "已保存配置，可以开始使用。"
      : "尚未检测到密钥，请确认是否填写正确。";
  } catch (err) {
    alert("保存失败：" + err.message);
  }
}

function closeSettings() {
  $("settings-modal").classList.add("hidden");
}

window.addEventListener("DOMContentLoaded", () => {
  $("btn-start").onclick = startSession;
  $("btn-stop").onclick = stopSession;
  $("btn-outline").onclick = summarize;
  $("btn-refresh").onclick = refreshSegments;
  $("btn-ask").onclick = ask;
  $("btn-quiz").onclick = generateQuiz;
  $("btn-report").onclick = generateReport;
  $("btn-recom").onclick = generateRecom;

  $("btn-settings").onclick = openSettings;
  $("btn-save-settings").onclick = saveSettings;
  $("btn-close-settings").onclick = closeSettings;
  $("settings-modal").onclick = (e) => {
    if (e.target === $("settings-modal")) closeSettings();
  };
});
