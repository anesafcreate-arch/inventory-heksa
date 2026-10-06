const toggleButton = document.querySelector("[data-toggle-password]");
const passwordInput = document.querySelector("#password");
const appShell = document.querySelector("[data-app-shell]");
const openSidebarButton = document.querySelector("[data-sidebar-open]");
const closeSidebarButton = document.querySelector("[data-sidebar-close]");
const characterCounter = document.querySelector("[data-character-counter]");
const counterValue = document.querySelector("[data-counter-value]");
const barcodeInput = document.querySelector("[data-barcode-input]");
const generateBarcodeButton = document.querySelector("[data-generate-barcode]");
const barcodePreview = document.querySelector("[data-barcode-live-preview]");
const deleteModal = document.querySelector("[data-delete-modal]");
const deleteForm = document.querySelector("[data-delete-form]");
const deleteLabel = document.querySelector("[data-delete-label]");
const actionMenu = document.querySelector("[data-action-menu]");
const actionDetailLink = document.querySelector("[data-action-detail]");
const actionEditLink = document.querySelector("[data-action-edit]");
const actionPrintLink = document.querySelector("[data-action-print]");
const actionDownloadLink = document.querySelector("[data-action-download]");
const actionDeleteButton = document.querySelector("[data-action-delete]");
const scannerVideo = document.querySelector("[data-scanner-video]");
const scannerPlaceholder = document.querySelector("[data-camera-placeholder]");
const scannerStatus = document.querySelector("[data-scanner-status]");
const scannerStartButton = document.querySelector("[data-scanner-start]");
const scannerStopButton = document.querySelector("[data-scanner-stop]");
const scannerSwitchButton = document.querySelector("[data-scanner-switch]");
const manualBarcodeForm = document.querySelector("[data-manual-barcode-form]");
const manualBarcodeInput = document.querySelector("[data-manual-barcode-input]");
let activeActionButton = null;
let scannerStream = null;
let barcodeDetector = null;
let scannerRunning = false;
let scannerLocked = false;
let scannerFacingMode = "environment";

if (toggleButton && passwordInput) {
  toggleButton.addEventListener("click", () => {
    const isHidden = passwordInput.type === "password";
    passwordInput.type = isHidden ? "text" : "password";
    toggleButton.textContent = isHidden ? "Hide" : "Show";
    toggleButton.setAttribute(
      "aria-label",
      isHidden ? "Sembunyikan password" : "Tampilkan password"
    );
  });
}

if (appShell && openSidebarButton && closeSidebarButton) {
  const closeSidebar = () => {
    appShell.classList.remove("sidebar-open");
    openSidebarButton.setAttribute("aria-expanded", "false");
  };

  openSidebarButton.addEventListener("click", () => {
    const willOpen = !appShell.classList.contains("sidebar-open");
    appShell.classList.toggle("sidebar-open", willOpen);
    openSidebarButton.setAttribute("aria-expanded", String(willOpen));
  });

  closeSidebarButton.addEventListener("click", closeSidebar);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSidebar();
    }
  });
}

if (characterCounter && counterValue) {
  const updateCounter = () => {
    counterValue.textContent = characterCounter.value.length;
  };

  characterCounter.addEventListener("input", updateCounter);
  updateCounter();
}

if (barcodeInput && barcodePreview) {
  const previewImage = barcodePreview.querySelector("img");
  const updateBarcodePreview = () => {
    const value = barcodeInput.value.trim().toUpperCase();
    if (!value || !/^[A-Z0-9-]+$/.test(value)) {
      barcodePreview.hidden = true;
      return;
    }
    previewImage.src = `/equipment/barcode/${encodeURIComponent(value)}.png`;
    barcodePreview.hidden = false;
  };

  barcodeInput.addEventListener("input", () => {
    barcodeInput.value = barcodeInput.value.toUpperCase();
    updateBarcodePreview();
  });

  updateBarcodePreview();
}

if (generateBarcodeButton && barcodeInput) {
  generateBarcodeButton.addEventListener("click", async () => {
    generateBarcodeButton.disabled = true;
    generateBarcodeButton.textContent = "Generating...";
    try {
      const response = await fetch("/equipment/barcode/generate");
      const data = await response.json();
      barcodeInput.value = data.barcode;
      barcodeInput.dispatchEvent(new Event("input", { bubbles: true }));
    } finally {
      generateBarcodeButton.disabled = false;
      generateBarcodeButton.textContent = "Generate";
    }
  });
}

document.querySelectorAll("[data-detail-tab]").forEach((tabButton) => {
  tabButton.addEventListener("click", () => {
    const selectedTab = tabButton.dataset.detailTab;
    document.querySelectorAll("[data-detail-tab]").forEach((button) => {
      const isActive = button.dataset.detailTab === selectedTab;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-selected", String(isActive));
    });
    document.querySelectorAll("[data-detail-panel]").forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.detailPanel === selectedTab);
    });
  });
});

const setScannerStatus = (message, type = "info") => {
  if (!scannerStatus) {
    return;
  }
  scannerStatus.textContent = message;
  scannerStatus.dataset.status = type;
};

const stopScanner = () => {
  scannerRunning = false;
  if (scannerStream) {
    scannerStream.getTracks().forEach((track) => track.stop());
  }
  scannerStream = null;
  if (scannerVideo) {
    scannerVideo.srcObject = null;
  }
  scannerPlaceholder?.removeAttribute("hidden");
  if (scannerStartButton) {
    scannerStartButton.disabled = false;
  }
  if (scannerStopButton) {
    scannerStopButton.disabled = true;
  }
  if (scannerSwitchButton) {
    scannerSwitchButton.disabled = true;
  }
};

const scannerErrorMessage = (error) => {
  if (!navigator.mediaDevices?.getUserMedia) {
    return "Browser ini belum mendukung fitur scan barcode.";
  }
  if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
    return "Akses kamera ditolak. Izinkan kamera pada pengaturan browser untuk menggunakan fitur scan.";
  }
  if (error?.name === "NotFoundError" || error?.name === "OverconstrainedError") {
    return "Kamera tidak ditemukan pada perangkat ini.";
  }
  if (error?.name === "NotReadableError" || error?.name === "AbortError") {
    return "Kamera sedang tidak tersedia.";
  }
  return "Kamera sedang tidak tersedia.";
};

const lookupBarcode = async (barcodeValue, { fromScanner = false } = {}) => {
  const cleanBarcode = barcodeValue.trim();
  if (!cleanBarcode) {
    setScannerStatus("Barcode wajib diisi.", "error");
    if (fromScanner) {
      stopScanner();
    }
    return;
  }

  if (!/^[A-Za-z0-9-]+$/.test(cleanBarcode)) {
    setScannerStatus("Barcode hanya boleh memakai huruf, angka, dan tanda hubung.", "error");
    if (fromScanner) {
      stopScanner();
    }
    return;
  }

  if (fromScanner) {
    scannerLocked = true;
  }

  try {
    setScannerStatus("Mencari data alat...", "info");
    const response = await fetch(`/api/equipment/barcode/${encodeURIComponent(cleanBarcode)}`);
    const data = await response.json();
    if (response.ok) {
      stopScanner();
      window.location.href = data.detail_url;
      return;
    }
    setScannerStatus(data.message || "Barcode tidak ditemukan.", "error");
    if (fromScanner) {
      stopScanner();
    }
  } catch (error) {
    setScannerStatus("Barcode tidak ditemukan.", "error");
    if (fromScanner) {
      stopScanner();
    }
  } finally {
    if (!fromScanner) {
      scannerLocked = false;
    }
  }
};

const scanFrame = async () => {
  if (!scannerRunning || scannerLocked || !barcodeDetector || !scannerVideo) {
    return;
  }

  try {
    const detectedBarcodes = await barcodeDetector.detect(scannerVideo);
    if (detectedBarcodes.length > 0) {
      const rawValue = detectedBarcodes[0].rawValue || "";
      await lookupBarcode(rawValue, { fromScanner: true });
      return;
    }
  } catch (error) {
    setScannerStatus("Kamera sedang tidak tersedia.", "error");
    stopScanner();
    return;
  }

  if (scannerRunning) {
    if ("requestVideoFrameCallback" in HTMLVideoElement.prototype) {
      scannerVideo.requestVideoFrameCallback(scanFrame);
    } else {
      window.setTimeout(scanFrame, 250);
    }
  }
};

const startScanner = async () => {
  if (!scannerVideo || !scannerStartButton) {
    return;
  }

  if (!("BarcodeDetector" in window)) {
    setScannerStatus("Browser ini belum mendukung fitur scan barcode.", "error");
    return;
  }

  try {
    const supportedFormats = await window.BarcodeDetector.getSupportedFormats();
    if (!supportedFormats.includes("code_128")) {
      setScannerStatus("Browser ini belum mendukung fitur scan barcode.", "error");
      return;
    }
    barcodeDetector = new window.BarcodeDetector({ formats: ["code_128"] });
    scannerLocked = false;
    scannerStartButton.disabled = true;
    setScannerStatus("Meminta akses kamera...", "info");

    scannerStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: scannerFacingMode },
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
      audio: false,
    });

    scannerVideo.srcObject = scannerStream;
    await scannerVideo.play();
    scannerRunning = true;
    scannerPlaceholder?.setAttribute("hidden", "");
    scannerStopButton.disabled = false;
    scannerSwitchButton.disabled = false;
    setScannerStatus("Arahkan barcode ke dalam frame.", "success");
    scanFrame();
  } catch (error) {
    stopScanner();
    setScannerStatus(scannerErrorMessage(error), "error");
  }
};

if (scannerStartButton) {
  scannerStartButton.addEventListener("click", startScanner);
}

if (scannerStopButton) {
  scannerStopButton.addEventListener("click", () => {
    stopScanner();
    scannerLocked = false;
    setScannerStatus("Kamera dihentikan. Tekan Start Camera untuk mulai lagi.", "info");
  });
}

if (scannerSwitchButton) {
  scannerSwitchButton.addEventListener("click", async () => {
    scannerFacingMode = scannerFacingMode === "environment" ? "user" : "environment";
    stopScanner();
    await startScanner();
  });
}

if (manualBarcodeForm && manualBarcodeInput) {
  manualBarcodeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await lookupBarcode(manualBarcodeInput.value, { fromScanner: false });
  });
}

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    if (form.matches("[data-manual-barcode-form]")) {
      return;
    }
    const submitButton = form.querySelector("button[type='submit']");
    if (!submitButton || submitButton.dataset.keepEnabled === "true") {
      return;
    }
    submitButton.dataset.originalText = submitButton.textContent;
    submitButton.textContent = "Processing...";
    window.setTimeout(() => {
      submitButton.disabled = true;
    }, 0);
  });
});

const closeActionMenu = () => {
  if (!actionMenu) {
    return;
  }
  actionMenu.hidden = true;
  actionMenu.classList.remove("is-open");
  if (activeActionButton) {
    activeActionButton.setAttribute("aria-expanded", "false");
  }
  activeActionButton = null;
};

const openDeleteModal = (equipmentId, equipmentNo, equipmentName) => {
  if (!deleteModal || !deleteForm || !deleteLabel) {
    return;
  }
  closeActionMenu();
  deleteForm.action = `/equipment/${equipmentId}/delete`;
  deleteLabel.textContent = `${equipmentNo} - ${equipmentName}`;
  deleteModal.classList.add("is-visible");
};

const positionActionMenu = (button) => {
  if (!actionMenu) {
    return;
  }
  const rect = button.getBoundingClientRect();
  const spacing = 8;
  const viewportPadding = 12;

  actionMenu.hidden = false;
  actionMenu.classList.add("is-open");
  actionMenu.style.left = "0px";
  actionMenu.style.top = "0px";

  const menuRect = actionMenu.getBoundingClientRect();
  const menuWidth = menuRect.width;
  const menuHeight = menuRect.height;
  const roomBelow = window.innerHeight - rect.bottom - spacing - viewportPadding;
  const roomAbove = rect.top - spacing - viewportPadding;

  let left = rect.right - menuWidth;
  let top = rect.bottom + spacing;

  if (roomBelow < menuHeight && roomAbove > roomBelow) {
    top = rect.top - menuHeight - spacing;
  }

  left = Math.max(
    viewportPadding,
    Math.min(left, window.innerWidth - menuWidth - viewportPadding)
  );
  top = Math.max(
    viewportPadding,
    Math.min(top, window.innerHeight - menuHeight - viewportPadding)
  );

  actionMenu.style.left = `${left}px`;
  actionMenu.style.top = `${top}px`;
};

const openActionMenu = (button) => {
  if (
    !actionMenu ||
    !actionDetailLink ||
    !actionEditLink ||
    !actionPrintLink ||
    !actionDownloadLink
  ) {
    return;
  }

  if (activeActionButton === button && !actionMenu.hidden) {
    closeActionMenu();
    return;
  }

  closeActionMenu();
  activeActionButton = button;
  activeActionButton.setAttribute("aria-expanded", "true");

  actionDetailLink.href = button.dataset.detailUrl;
  actionEditLink.href = button.dataset.editUrl;
  actionPrintLink.href = button.dataset.printUrl;
  actionDownloadLink.href = button.dataset.downloadUrl;

  if (actionDeleteButton) {
    actionDeleteButton.dataset.deleteId = button.dataset.equipmentId;
    actionDeleteButton.dataset.deleteNo = button.dataset.equipmentNo;
    actionDeleteButton.dataset.deleteName = button.dataset.equipmentName;
  }

  positionActionMenu(button);
};

document.addEventListener("click", (event) => {
  const menuButton = event.target.closest("[data-action-menu-button]");
  const deleteButton = event.target.closest("[data-action-delete]");

  if (menuButton) {
    event.preventDefault();
    event.stopPropagation();
    openActionMenu(menuButton);
    return;
  }

  if (deleteButton) {
    event.preventDefault();
    event.stopPropagation();
    openDeleteModal(
      deleteButton.dataset.deleteId,
      deleteButton.dataset.deleteNo,
      deleteButton.dataset.deleteName
    );
    return;
  }

  if (actionMenu && !actionMenu.hidden && !actionMenu.contains(event.target)) {
    closeActionMenu();
  }
});

document.querySelectorAll("[data-delete-close]").forEach((button) => {
  button.addEventListener("click", () => {
    deleteModal?.classList.remove("is-visible");
  });
});

if (deleteModal) {
  deleteModal.addEventListener("click", (event) => {
    if (event.target === deleteModal) {
      deleteModal.classList.remove("is-visible");
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeActionMenu();
    if (deleteModal?.classList.contains("is-visible")) {
      deleteModal.classList.remove("is-visible");
    }
  }
});

window.addEventListener("resize", () => {
  if (activeActionButton && actionMenu && !actionMenu.hidden) {
    positionActionMenu(activeActionButton);
  }
});

window.addEventListener(
  "scroll",
  () => {
    closeActionMenu();
  },
  true
);
