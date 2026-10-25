// app.js — Shared prediction logic and result rendering
// Loaded first so camera.js, upload.js and map.js can call sendForPrediction().

let currentPrediction = null;

/**
 * Safely escapes HTML to prevent XSS when rendering API-sourced text.
 */
function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/**
 * Converts basic Gemini markdown to safe HTML for display.
 * Operates on already-escaped text so only the markdown tokens are trusted.
 */
function markdownToHtml(raw) {
  return escapeHtml(raw)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/^[\*\-]\s+/gm, "• ")
    .replace(/\n/g, "<br>");
}

/**
 * Sends a base64-encoded image to /predict and renders the result.
 * @param {string} imageData  base64 data-URL (image/jpeg)
 */
async function sendForPrediction(imageData) {
  const spinner = document.getElementById("loadingSpinner");
  const resultCard = document.getElementById("resultCard");
  const placeholderCard = document.getElementById("placeholderCard");
  const captureBtn = document.getElementById("captureBtn");
  const classifyUploadBtn = document.getElementById("classifyUploadBtn");

  if (window.authRequireUser) {
    const allowed = await window.authRequireUser();
    if (!allowed) return;
  }

  const authToken = window.getAuthToken ? window.getAuthToken() : "";

  // Show spinner, lock buttons
  spinner.style.display = "block";
  if (captureBtn) captureBtn.disabled = true;
  if (classifyUploadBtn) classifyUploadBtn.disabled = true;
  resultCard.style.display = "none";
  placeholderCard.style.display = "none";

  fetch("/predict", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: authToken ? `Bearer ${authToken}` : "",
    },
    body: JSON.stringify({ image: imageData }),
  })
    .then((res) => res.json())
    .then((data) => {
      spinner.style.display = "none";
      if (captureBtn) captureBtn.disabled = false;
      if (classifyUploadBtn) classifyUploadBtn.disabled = false;

      if (data.error) {
        if (
          data.error.toLowerCase().includes("authentication") &&
          window.showAuthModal
        ) {
          window.showAuthModal();
        }
        showPredictionError(escapeHtml(data.error));
        return;
      }
      displayResult(data);
    })
    .catch((err) => {
      spinner.style.display = "none";
      if (captureBtn) captureBtn.disabled = false;
      if (classifyUploadBtn) classifyUploadBtn.disabled = false;
      showPredictionError("Connection error. Please try again.");
      console.error(err);
    });
}

function displayResult(data) {
  const resultCard = document.getElementById("resultCard");
  const placeholderCard = document.getElementById("placeholderCard");
  currentPrediction = {
    type: String(data.type || ""),
    confidence: parseFloat(data.confidence) || 0,
  };

  // Plastic type badge
  document.getElementById("plasticTypeLabel").textContent = escapeHtml(
    data.type,
  );

  // Confidence bar
  const conf = parseFloat(data.confidence) || 0;
  document.getElementById("confidenceText").textContent = conf + "%";
  const bar = document.getElementById("confidenceBar");
  const boundedConf = Math.max(0, Math.min(100, conf));
  bar.style.width = boundedConf + "%";
  if (boundedConf >= 75) {
    bar.style.background = "linear-gradient(135deg, #006e1c 0%, #4caf50 100%)";
  } else if (boundedConf >= 45) {
    bar.style.background = "linear-gradient(135deg, #8b7a09 0%, #d4b10b 100%)";
  } else {
    bar.style.background = "linear-gradient(135deg, #8a1c1c 0%, #d64545 100%)";
  }

  // Reuse list (desi ideas shown in quotes)
  const reuseList = document.getElementById("reuseList");
  if (Array.isArray(data.reuse)) {
    reuseList.innerHTML = data.reuse
      .map((item) => {
        const cleanItem = String(item)
          .trim()
          .replace(/^"+|"+$/g, "");
        return `<li class="mb-0.5 leading-6">"${escapeHtml(cleanItem)}"</li>`;
      })
      .join("");
  }

  // Recycling instructions (2-3 pointwise)
  const recycleInfo = document.getElementById("recycleInfo");
  const recycleItems = Array.isArray(data.recycle_instructions)
    ? data.recycle_instructions
    : [];

  if (recycleItems.length) {
    recycleInfo.innerHTML = recycleItems
      .map((item) => `<li class="mb-0.5 leading-6">${escapeHtml(item)}</li>`)
      .join("");
  } else {
    recycleInfo.innerHTML =
      '<li class="mb-0.5 leading-6">Recycling instructions are currently unavailable.</li>';
  }

  // AI advice — Gemini markdown converted to safe HTML
  const aiText = document.getElementById("aiAdviceText");
  if (data.ai_advice) {
    aiText.innerHTML = markdownToHtml(data.ai_advice);
  } else {
    aiText.textContent = "AI advice unavailable.";
  }

  updateSortBotTarget(data.sortbot);
  refreshSortBotStatus();
  placeholderCard.style.display = "none";
  resultCard.style.display = "block";

  // On mobile, scroll result into view
  if (window.innerWidth < 992) {
    resultCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function updateSortBotTarget(target) {
    const targetEl = document.getElementById("sortbotTarget");
    const button = document.getElementById("sortbotSortBtn");
    if (!targetEl || !button) return;

    if (target && target.bin) {
      targetEl.textContent = `Predicted ${currentPrediction.type} is assigned to Bin ${target.bin}.`;
    } else {
      targetEl.textContent = "This prediction is not mapped to a physical bin.";
    }

    button.disabled = !target?.eligible;
    if (target?.reason) {
      setSortBotMessage(target.reason, "text-on-secondary-container");
    }
  }

  async function refreshSortBotStatus() {
    const stateEl = document.getElementById("sortbotConnectionState");
    if (!stateEl) return;

    if (window.authRequireUser) {
      const allowed = await window.authRequireUser();
      if (!allowed) {
        stateEl.textContent = "Sign in required";
        return;
      }
    }

    const authToken = window.getAuthToken ? window.getAuthToken() : "";
    try {
      const response = await fetch("/sortbot/status", {
        headers: { Authorization: authToken ? `Bearer ${authToken}` : "" },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Status unavailable.");
      stateEl.textContent = data.connected ? "Bluetooth connected" : "Not connected";
      stateEl.className = data.connected
        ? "text-xs font-semibold text-primary"
        : "text-xs font-semibold text-on-secondary-container";
      if (!data.connected && data.message) setSortBotMessage(data.message);
    } catch (error) {
      stateEl.textContent = "Status unavailable";
      setSortBotMessage(error.message || "Unable to read SortBot status.");
    }
  }

  function setSortBotMessage(message, className = "text-on-secondary-container") {
    const messageEl = document.getElementById("sortbotStatusMessage");
    if (!messageEl) return;
    messageEl.textContent = message;
    messageEl.className = `text-sm mb-0 ${className}`;
  }

  async function sortCurrentPrediction() {
    const button = document.getElementById("sortbotSortBtn");
    if (!button || !currentPrediction) return;

    if (window.authRequireUser) {
      const allowed = await window.authRequireUser();
      if (!allowed) return;
    }

    button.disabled = true;
    setSortBotMessage("Sending the selected bin command...");
    const authToken = window.getAuthToken ? window.getAuthToken() : "";

    try {
      const response = await fetch("/sort", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: authToken ? `Bearer ${authToken}` : "",
        },
        body: JSON.stringify(currentPrediction),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "SortBot command failed.");
      setSortBotMessage(data.message || "Command sent to SortBot.");
    } catch (error) {
      setSortBotMessage(error.message || "Unable to send the SortBot command.", "text-red-700");
      refreshSortBotStatus();
    }
  }

  window.sortCurrentPrediction = sortCurrentPrediction;
}

function showPredictionError(safeMessage) {
  const placeholderCard = document.getElementById("placeholderCard");
  placeholderCard.innerHTML = `
    <i class="bi bi-exclamation-circle-fill display-4 text-danger"></i>
    <p class="text-danger mt-3">${safeMessage}</p>
    <p class="text-muted small">Check the console for details.</p>
  `;
  placeholderCard.style.display = "block";
}

function scrollToMapSection() {
  const locationsSection = document.getElementById("locationsSection");
  if (locationsSection) {
    locationsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
