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
  requestAnimationFrame(() => {
    toast.classList.add("visible");
  });

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

const pendingToast = window.sessionStorage.getItem("dentvoice_toast");
if (pendingToast) {
  showToast(pendingToast);
  window.sessionStorage.removeItem("dentvoice_toast");
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

document.getElementById("settings-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const form = event.currentTarget;
    await postJson("/api/settings", {
      clinic_name: form.clinic_name.value,
      clinic_timings: form.clinic_timings.value,
      clinic_address: form.clinic_address.value,
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
