async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Request failed");
  }

  return response.json();
}

async function postForm(url, formData) {
  const response = await fetch(url, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Request failed");
  }

  return response.json();
}

function showToast(message, kind = "success") {
  const toast = document.getElementById("toast");
  if (!toast) {
    return;
  }

  toast.textContent = message;
  toast.dataset.kind = kind;
  toast.hidden = false;
  requestAnimationFrame(() => toast.classList.add("visible"));

  window.clearTimeout(showToast._timeoutId);
  showToast._timeoutId = window.setTimeout(() => {
    toast.classList.remove("visible");
    window.setTimeout(() => {
      toast.hidden = true;
    }, 220);
  }, 2600);
}

function refreshPage(message) {
  if (message) {
    window.sessionStorage.setItem("dentvoice_toast", message);
  }
  window.location.reload();
}

function restorePendingToast() {
  const pendingToast = window.sessionStorage.getItem("dentvoice_toast");
  if (!pendingToast) {
    return;
  }
  showToast(pendingToast);
  window.sessionStorage.removeItem("dentvoice_toast");
}

function getFilterStorageKey(key) {
  return `dentvoice_saved_filters_${key}`;
}

function serializeForm(form) {
  const formData = new FormData(form);
  const entries = {};
  for (const [key, value] of formData.entries()) {
    entries[key] = value;
  }

  form.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    entries[input.name] = input.checked ? input.value : "";
  });
  return entries;
}

function loadSavedFilters(key) {
  try {
    return JSON.parse(window.localStorage.getItem(getFilterStorageKey(key)) || "[]");
  } catch {
    return [];
  }
}

function persistSavedFilters(key, filters) {
  window.localStorage.setItem(getFilterStorageKey(key), JSON.stringify(filters));
}

function applyFilterToForm(form, filter) {
  Object.entries(filter.values).forEach(([name, value]) => {
    const element = form.elements.namedItem(name);
    if (!element) {
      return;
    }
    if (element.type === "checkbox") {
      element.checked = value === element.value;
      return;
    }
    element.value = value;
  });
  form.submit();
}

function renderSavedFilters(form, key) {
  const list = document.querySelector(`[data-saved-filter-list="${key}"]`);
  if (!list) {
    return;
  }

  const filters = loadSavedFilters(key);
  list.innerHTML = "";

  if (!filters.length) {
    list.innerHTML = '<span class="empty-inline">No saved filters yet.</span>';
    return;
  }

  filters.forEach((filter, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "saved-filter-pill";
    button.textContent = filter.name;
    button.addEventListener("click", () => applyFilterToForm(form, filter));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "saved-filter-delete";
    deleteButton.textContent = "x";
    deleteButton.addEventListener("click", () => {
      const nextFilters = loadSavedFilters(key).filter((_, itemIndex) => itemIndex !== index);
      persistSavedFilters(key, nextFilters);
      renderSavedFilters(form, key);
      showToast("Saved filter removed");
    });

    const wrapper = document.createElement("div");
    wrapper.className = "saved-filter-chip";
    wrapper.append(button, deleteButton);
    list.append(wrapper);
  });
}

function setupSavedFilters() {
  document.querySelectorAll("form[data-save-filters]").forEach((form) => {
    const key = form.dataset.saveFilters;
    renderSavedFilters(form, key);
  });

  document.querySelectorAll(".save-filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.saveFilterTarget;
      const form = document.querySelector(`form[data-save-filters="${key}"]`);
      if (!form) {
        return;
      }

      const filters = loadSavedFilters(key);
      const name = window.prompt("Name this filter view:");
      if (!name) {
        return;
      }

      filters.push({ name, values: serializeForm(form) });
      persistSavedFilters(key, filters);
      renderSavedFilters(form, key);
      showToast("Filter saved");
    });
  });

  document.querySelectorAll(".clear-filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.clearFilterTarget;
      window.localStorage.removeItem(getFilterStorageKey(key));
      const form = document.querySelector(`form[data-save-filters="${key}"]`);
      if (form) {
        renderSavedFilters(form, key);
      }
      showToast("Saved filters cleared");
    });
  });
}

function setupOnboarding() {
  const modal = document.getElementById("onboarding-modal");
  if (!modal) {
    return;
  }

  const onboardingKey = `dentvoice_onboarding_seen_${modal.dataset.onboarding || "default"}`;
  if (window.localStorage.getItem(onboardingKey) === "true") {
    return;
  }

  const steps = [...modal.querySelectorAll(".onboarding-step")];
  const nextButton = document.getElementById("next-onboarding");
  const skipButton = document.getElementById("skip-onboarding");
  const backButton = document.getElementById("back-onboarding");
  const progressLabel = document.getElementById("onboarding-progress");
  let currentStep = 0;

  const syncSteps = () => {
    steps.forEach((step, index) => {
      step.classList.toggle("is-active", index === currentStep);
      step.hidden = index !== currentStep;
    });
    if (progressLabel) {
      progressLabel.textContent = `Step ${currentStep + 1} of ${steps.length}`;
    }
    if (nextButton) {
      nextButton.textContent = currentStep === steps.length - 1 ? "Finish" : "Next";
    }
    if (backButton) {
      backButton.disabled = currentStep === 0;
    }
  };

  const closeModal = () => {
    window.localStorage.setItem(onboardingKey, "true");
    modal.hidden = true;
  };

  modal.hidden = false;
  syncSteps();

  nextButton?.addEventListener("click", () => {
    if (currentStep >= steps.length - 1) {
      closeModal();
      return;
    }
    currentStep += 1;
    syncSteps();
  });

  backButton?.addEventListener("click", () => {
    if (currentStep === 0) {
      return;
    }
    currentStep -= 1;
    syncSteps();
  });

  skipButton?.addEventListener("click", closeModal);
}

restorePendingToast();

document.getElementById("seed-demo")?.addEventListener("click", async () => {
  try {
    await postJson("/api/demo/seed", {});
    refreshPage("Demo data loaded");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("simulate-call")?.addEventListener("click", async () => {
  try {
    const slotsResponse = await fetch("/api/available-slots");
    const slotData = await slotsResponse.json();
    const fallbackSlot = slotData.slots?.[0];
    await postJson("/api/simulate-call", {
      caller_number: "+919876543210",
      patient_name: "Demo Patient",
      transcript: "Hello, I want to book an appointment for teeth cleaning this Friday evening",
      preferred_date: fallbackSlot?.date,
      preferred_time: fallbackSlot?.time,
      reason_for_visit: "Teeth cleaning",
    });
    refreshPage("Simulated call added");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("contact-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/contact-request", formData);
    event.currentTarget.reset();
    showToast("Demo request submitted");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("appointment-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/admin/appointments", formData);
    refreshPage("Appointment saved");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("task-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/receptionist-tasks", formData);
    refreshPage("Receptionist task created");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".task-edit-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/receptionist-tasks/${form.dataset.taskId}/update`, formData);
      refreshPage("Receptionist task updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.getElementById("settings-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    await postJson("/api/settings", {
      clinic_name: form.clinic_name.value,
      clinic_timings: form.clinic_timings.value,
      clinic_address: form.clinic_address.value,
      brand_tagline: form.brand_tagline.value,
      accent_color: form.accent_color.value,
      logo_text: form.logo_text.value,
      admin_username: form.admin_username.value,
      admin_password: form.admin_password.value,
    });
    refreshPage("Clinic settings updated");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("slot-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    await postJson("/api/slots", {
      date: form.date.value,
      time: form.time.value,
    });
    refreshPage("Slot added");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.querySelectorAll(".slot-delete").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await postForm(`/api/slots/${button.dataset.slotId}/delete`, new FormData());
      refreshPage("Slot removed");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".appointment-edit-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/appointments/${form.dataset.appointmentId}/update`, formData);
      refreshPage("Appointment updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".appointment-delete").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("Delete this appointment?")) {
      return;
    }
    try {
      await postForm(`/api/appointments/${button.dataset.appointmentId}/delete`, new FormData());
      refreshPage("Appointment deleted");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".call-score-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/calls/${form.dataset.callId}/lead-score`, formData);
      refreshPage("Lead score updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

setupSavedFilters();
setupOnboarding();
