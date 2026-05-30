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

restorePendingToast();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/service-worker.js").catch(() => {});
  });
}

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

document.querySelectorAll(".contact-request-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/contact-requests/${form.dataset.requestId}/update`, formData);
      refreshPage("Demo request updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".faq-create-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm("/api/faqs", formData);
      refreshPage("FAQ added");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".faq-edit-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/faqs/${form.dataset.faqId}/update`, formData);
      refreshPage("FAQ updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".faq-delete").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("Delete this FAQ?")) {
      return;
    }
    try {
      await postForm(`/api/faqs/${button.dataset.faqId}/delete`, new FormData());
      refreshPage("FAQ deleted");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
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

document.getElementById("clinic-switch-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/clinic/switch", formData);
    refreshPage("Clinic switched");
  } catch (error) {
    showToast(error.message, "error");
  }
});

document.getElementById("clinic-create-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const formData = new FormData(event.currentTarget);
    await postForm("/api/clinics", formData);
    refreshPage("Clinic workspace created");
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

document.querySelectorAll(".missed-lead-task-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/missed-leads/${form.dataset.callId}/task`, formData);
      refreshPage("Recovery task created");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".reminder-create-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm("/api/reminders", formData);
      refreshPage("Reminder queued");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

document.querySelectorAll(".reminder-update-form").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const formData = new FormData(form);
      await postForm(`/api/reminders/${form.dataset.reminderId}/update`, formData);
      refreshPage("Reminder updated");
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
      working_days: form.working_days.value,
      working_hours: form.working_hours.value,
      auto_callback_enabled: form.auto_callback_enabled.checked,
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
      await postForm(`/api/calls/${form.dataset.callId}/update`, formData);
      refreshPage("Call record updated");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
});

setupSavedFilters();
