const $ = (sel) => document.querySelector(sel);

async function loadHealth() {
  const pill = $("#status-pill");
  try {
    const res = await fetch("/api/healthz");
    const data = await res.json();

    const caps = data.capabilities || {};
    const missing = Object.entries(caps)
      .filter(([, on]) => !on)
      .map(([name]) => name);

    if (missing.length === 0) {
      pill.textContent = "all systems configured";
      pill.className = "pill pill-ok";
    } else {
      pill.textContent = `not configured: ${missing.join(", ")}`;
      pill.className = "pill pill-muted";
    }
    $("#version").textContent = `v${data.version} · ${data.environment}`;
  } catch (err) {
    pill.textContent = "unreachable";
    pill.className = "pill pill-bad";
  }
}

function wireNav() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      document
        .querySelectorAll(".nav-item")
        .forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      const name = btn.dataset.view;
      $("#main").innerHTML = `
        <section class="view">
          <div class="placeholder">
            <h2>${btn.textContent}</h2>
            <p>Not built yet (<code>${name}</code>).</p>
          </div>
        </section>`;
    });
  });
}

wireNav();
loadHealth();
