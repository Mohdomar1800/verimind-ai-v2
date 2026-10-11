// Upload page: drag-and-drop (files and folders), file list, and upload progress bar.
// The form also works without this script (plain multipart POST to /upload).
(() => {
  const ALLOWED = /\.(log|out|txt)(\.gz)?$|\.zip$/i;
  const form = document.getElementById("upload-form");
  const input = document.getElementById("files");
  const zone = document.getElementById("dropzone");
  const errorBox = document.getElementById("upload-error");
  let chosen = [];

  const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };
  const fmtSize = (b) => b < 1024 ** 2 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1024 ** 2).toFixed(1)} MB`;

  function render() {
    document.getElementById("file-summary").hidden = chosen.length === 0;
    document.getElementById("file-count").textContent = `${chosen.length} file${chosen.length === 1 ? "" : "s"} selected`;
    document.getElementById("file-size").textContent = `(${fmtSize(chosen.reduce((s, f) => s + f.size, 0))})`;
    const list = document.getElementById("file-list");
    list.replaceChildren(...chosen.slice(0, 8).map((f) => {
      const li = document.createElement("li");
      li.textContent = f.relativePath || f.name;
      return li;
    }));
    if (chosen.length > 8) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = `…and ${chosen.length - 8} more`;
      list.append(li);
    }
  }

  function add(files) {
    const skipped = [];
    for (const f of files) (ALLOWED.test(f.name) ? chosen : skipped).push(f);
    showError(skipped.length ? `Skipped ${skipped.length} file(s) that are not logs (e.g. ${skipped[0].name}).` : "");
    render();
  }

  // Folders dropped onto the page are walked recursively (Chrome, Edge, Firefox, Safari).
  async function filesFromEntry(entry, prefix = "") {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      file.relativePath = prefix + file.name;
      return [file];
    }
    const reader = entry.createReader();
    const out = [];
    let batch;
    do {
      batch = await new Promise((res, rej) => reader.readEntries(res, rej));
      for (const child of batch) out.push(...await filesFromEntry(child, `${prefix}${entry.name}/`));
    } while (batch.length);
    return out;
  }

  input.addEventListener("change", () => { add(input.files); input.value = ""; });
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("over")));
  zone.addEventListener("drop", async (e) => {
    e.preventDefault();
    const entries = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (entries.length) {
      for (const entry of entries) add(await filesFromEntry(entry));
    } else {
      add(e.dataTransfer.files);
    }
  });
  document.getElementById("clear-files").addEventListener("click", () => { chosen = []; showError(""); render(); });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!chosen.length) { showError("Please choose at least one log file."); return; }
    const data = new FormData();
    for (const f of chosen) data.append("files", f, f.relativePath || f.name);
    data.append("name", form.elements.name.value);

    const bar = document.getElementById("upload-bar");
    const status = document.getElementById("upload-status");
    document.getElementById("upload-progress").hidden = false;
    document.getElementById("submit").disabled = true;
    showError("");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/runs");
    xhr.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      const pct = Math.round((100 * ev.loaded) / ev.total);
      bar.style.width = `${pct}%`;
      status.textContent = pct < 100 ? `Uploading… ${pct}%` : "Upload complete. Starting analysis…";
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (_) { /* not JSON */ }
      if (xhr.status === 202) { window.location = `/runs/${body.run.id}`; return; }
      document.getElementById("submit").disabled = false;
      document.getElementById("upload-progress").hidden = true;
      showError(typeof body.detail === "string" ? body.detail : "The upload failed. Please try again.");
    };
    xhr.onerror = () => {
      document.getElementById("submit").disabled = false;
      showError("Could not reach VeriMind. Check that it is still running and try again.");
    };
    xhr.send(data);
  });
})();
