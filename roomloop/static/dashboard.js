"use strict";

const $ = (selector) => document.querySelector(selector);
const rooms = JSON.parse($("#room-data").textContent);
const roomMap = new Map(rooms.map((room) => [room.id, room]));
const form = $("#booking-form");
const pageSize = 50;
let office = "all";
let bookingRows = [];
let seriesRows = [];
let refreshVersion = 0;
let formVersion = 0;
let pendingCancellation = null;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function feedback(target, message, kind = "success") {
  target.replaceChildren(element("p", message, `notice ${kind}`));
  target.hidden = false;
}

function officeName(timezone) {
  return timezone === "Europe/Berlin" ? "Berlin" : "Denver";
}

function dateLabel(value) {
  // Format a calendar date, never interpret a room-local timestamp in the browser's timezone.
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  }).format(date);
}

function timeRange(item) {
  const endDate = item.end.slice(0, 10) === item.start.slice(0, 10) ? "" : `${dateLabel(item.end)} `;
  return `${item.start.slice(11, 16)} – ${endDate}${item.end.slice(11, 16)}`;
}

async function api(path, payload) {
  let response;
  try {
    response = await fetch(path, payload === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
  } catch {
    throw new Error(payload === undefined ? "Could not load data. Check your connection and refresh." :
      "Connection interrupted. The request may have completed. Check the calendar before trying again.");
  }
  let data;
  try { data = await response.json(); } catch {
    throw new Error("The server returned an unreadable response. Refresh the calendar before retrying.");
  }
  if (!response.ok) {
    const error = new Error(data.error?.message || "The request could not be completed.");
    error.details = data.error?.details;
    throw error;
  }
  return data;
}

function showError(target, error) {
  let message = error.message;
  if (Array.isArray(error.details) && error.details.length) {
    message += " " + error.details.map((detail) => `${detail.field}: ${detail.message}`).join("; ");
  }
  feedback(target, message, "error");
  if (error.details?.skipped) renderOccurrences([], error.details.skipped, "No dates booked");
}

function requestBody() {
  const payload = {room_id: Number($("#room").value), user: $("#user").value.trim(),
    start: $("#start").value, end: $("#end").value};
  if (isWeekly()) Object.assign(payload, {rule: "weekly", repeat_until: $("#repeat-until").value});
  return payload;
}

function isWeekly() { return form.elements.kind.value === "weekly"; }

function invalidatePreview() {
  formVersion += 1;
  $("#preview-result").hidden = true;
  $("#form-feedback").hidden = true;
}

function updateKind() {
  const weekly = isWeekly();
  $("#recurrence-fields").hidden = !weekly;
  $("#repeat-until").disabled = !weekly;
  $("#repeat-until").required = weekly;
  $("#preview-button").hidden = !weekly;
  $("#submit-button").textContent = weekly ? "Create weekly series →" : "Create booking →";
  $("#booking-note").textContent = weekly ?
    "Clashing dates are skipped. All other dates are saved together. Preview to see the full picture." :
    "Times follow the room’s timezone, even when daylight saving changes.";
}

function selectRoom(id) {
  $("#room").value = String(id);
  const room = roomMap.get(id);
  $("#timezone-hint").textContent = `${room.timezone} · Room-local time`;
  document.querySelectorAll(".room-card").forEach((card) => {
    const selected = Number(card.dataset.room) === id;
    card.classList.toggle("selected", selected);
    card.setAttribute("aria-pressed", String(selected));
    card.querySelector(".selection-mark").textContent = selected ? "✓" : "↗";
  });
  invalidatePreview();
}

function setFormBusy(busy) {
  form.querySelectorAll("button,input,select").forEach((node) => { node.disabled = busy; });
  document.querySelectorAll(".room-card,[data-office]").forEach((node) => { node.disabled = busy; });
  if (busy) {
    // Do not leave an earlier availability result visible if this request fails.
    $("#preview-result").hidden = true;
    $("#form-feedback").hidden = true;
  }
  if (!busy) updateKind();
  form.setAttribute("aria-busy", String(busy));
}

function renderOccurrences(accepted, skipped, title, saved = false) {
  const target = $("#preview-result");
  target.replaceChildren(element("h3", title, "preview-title"),
    element("p", saved ? `${accepted.length} booked · ${skipped.length} skipped. All accepted dates have been saved.` :
      `${accepted.length} accepted · ${skipped.length} skipped. Preview does not reserve rooms; creation checks again.`, "preview-note"));
  const list = element("ul", undefined, "occurrence-list");
  const rows = [...accepted.map((item) => ({...item, skipped: false})),
    ...skipped.map((item) => ({...item, skipped: true}))].sort((a, b) => a.start.localeCompare(b.start));
  for (const item of rows) {
    const row = element("li", `${item.skipped ? "Skipped" : saved ? "Booked" : "Accepted"} · ${dateLabel(item.start)} · ${timeRange(item)}`,
      item.skipped ? "skipped" : "");
    row.append(element("small", `${officeName(item.timezone)} local time${item.skipped ? " · Room already booked during this time." : ""}`));
    if (item.skipped) {
      const details = element("details");
      details.append(element("summary", "Conflicting booking references"),
        element("code", item.conflicting_booking_ids.join(", ")));
      row.append(details);
    }
    list.append(row);
  }
  target.append(list);
  target.hidden = false;
}

function cancelButton(item, kind) {
  const button = element("button", kind === "series" ? "Cancel future" : "Cancel", "text-button cancel-button");
  button.type = "button";
  button.setAttribute("aria-label", `${kind === "series" ? "Cancel future dates for" : "Cancel booking for"} ${item.user} in ${roomMap.get(item.room_id).name}`);
  button.addEventListener("click", () => {
    pendingCancellation = {id: item.id, kind};
    $("#cancel-title").textContent = kind === "series" ? "Cancel future dates?" : "Cancel this booking?";
    $("#cancel-description").textContent = kind === "series" ?
      `This will release all not-yet-started dates for ${item.user} in ${roomMap.get(item.room_id).name}. Past and ongoing meetings stay unchanged.` :
      `Release ${roomMap.get(item.room_id).name} on ${dateLabel(item.start)}, ${timeRange(item)} (${officeName(item.timezone)} time). This cancels only this occurrence.`;
    $("#cancel-dialog").showModal();
  });
  return button;
}

function renderBookings() {
  const list = $("#booking-list");
  list.replaceChildren();
  if (!bookingRows.length) list.append(element("p", "A clear calendar. Make room for something good.", "empty-state"));
  for (const item of bookingRows) {
    const room = roomMap.get(item.room_id);
    const row = element("div", undefined, "booking-row");
    const content = element("div", undefined, "row-content");
    const heading = element("strong", `${room.name} · ${item.user}`);
    if (item.series_id) heading.append(element("span", "Weekly", "pill"));
    content.append(heading, element("p", `${officeName(item.timezone)} · ${room.capacity} seats`));
    const time = element("div", timeRange(item), "row-time");
    time.append(element("small", `${dateLabel(item.start)} · local time`));
    row.append(element("span", room.name[0], "room-token"), content, time, cancelButton(item, "bookings"));
    list.append(row);
  }
}

function renderSeries() {
  const list = $("#series-list");
  list.replaceChildren();
  if (!seriesRows.length) list.append(element("p", "Your weekly rituals will live here.", "empty-state"));
  for (const item of seriesRows) {
    const room = roomMap.get(item.room_id);
    const row = element("div", undefined, "booking-row series-row");
    const content = element("div", undefined, "row-content");
    const heading = element("strong", `${item.user} · ${room.name}`);
    heading.append(element("span", item.status === "cancelled" ? "Cancelled" : "Weekly", `pill ${item.status}`));
    content.append(heading, element("p", `${dateLabel(item.start)} – ${dateLabel(item.repeat_until)} · ${officeName(item.timezone)}`),
      element("p", `${timeRange(item)} local time · Same weekday each week`));
    row.append(element("span", "↻", "room-token"), content);
    if (item.status === "active") row.append(cancelButton(item, "series"));
    list.append(row);
  }
}

async function loadList(kind, append = false, version = refreshVersion) {
  const isBooking = kind === "bookings";
  const button = $(isBooking ? "#more-bookings" : "#more-series");
  const current = isBooking ? bookingRows : seriesRows;
  const query = new URLSearchParams({limit: String(pageSize), offset: String(append ? current.length : 0)});
  if (office !== "all") query.set("timezone", office);
  if (isBooking) { query.set("day", $("#calendar-date").value); query.set("status", "active"); }
  button.disabled = true;
  try {
    const rows = await api(`/${kind}?${query}`);
    if (version !== refreshVersion) return;
    if (isBooking) { bookingRows = append ? [...current, ...rows] : rows; renderBookings(); }
    else { seriesRows = append ? [...current, ...rows] : rows; renderSeries(); }
    button.hidden = rows.length < pageSize;
  } catch (error) {
    if (!append && version === refreshVersion) {
      $(isBooking ? "#booking-list" : "#series-list").replaceChildren(
        element("p", "Could not load this view. Reload the page to try again.", "empty-state"));
    }
    throw error;
  } finally { button.disabled = false; }
}

async function refresh() {
  const version = ++refreshVersion;
  $("#booking-list").replaceChildren(element("p", "Loading the calendar…", "empty-state"));
  $("#series-list").replaceChildren(element("p", "Loading your series…", "empty-state"));
  $("#more-bookings").hidden = true;
  $("#more-series").hidden = true;
  await Promise.all([loadList("bookings", false, version), loadList("series", false, version)]);
}

async function refreshAfterMutation() {
  try { await refresh(); } catch {
    feedback($("#page-feedback"), "Your change was saved, but the calendar could not refresh. Reload to see the latest bookings.", "warning");
  }
}

form.addEventListener("input", invalidatePreview);
form.elements.kind.forEach((radio) => radio.addEventListener("change", updateKind));
$("#room").addEventListener("change", () => selectRoom(Number($("#room").value)));
document.querySelectorAll(".room-card").forEach((card) => card.addEventListener("click", () => selectRoom(Number(card.dataset.room))));

document.querySelectorAll("[data-office]").forEach((button) => button.addEventListener("click", async () => {
  office = button.dataset.office;
  document.querySelectorAll("[data-office]").forEach((item) => {
    item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button));
  });
  document.querySelectorAll(".room-card").forEach((card) => { card.hidden = office !== "all" && card.dataset.timezone !== office; });
  if (office !== "all" && roomMap.get(Number($("#room").value)).timezone !== office) {
    selectRoom(rooms.find((room) => room.timezone === office).id);
  }
  try { await refresh(); } catch (error) { showError($("#page-feedback"), error); }
}));

$("#calendar-date").addEventListener("change", async () => {
  if (!$("#calendar-date").value) return;
  try { await refresh(); } catch (error) { showError($("#page-feedback"), error); }
});
for (const kind of ["bookings", "series"]) {
  $(`#more-${kind}`).addEventListener("click", async () => {
    try { await loadList(kind, true); } catch (error) { showError($("#page-feedback"), error); }
  });
}

$("#preview-button").addEventListener("click", async () => {
  if (!form.reportValidity()) return;
  const version = formVersion;
  const payload = requestBody();
  setFormBusy(true);
  try {
    const result = await api("/series/preview", payload);
    if (version === formVersion) renderOccurrences(result.available, result.skipped, "Your weekly dates");
  } catch (error) { showError($("#form-feedback"), error); }
  finally { setFormBusy(false); }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const payload = requestBody();
  const weekly = isWeekly();
  setFormBusy(true);
  try {
    const result = await api(weekly ? "/series" : "/bookings", payload);
    if (weekly) {
      feedback($("#form-feedback"), `${result.created.length} dates booked · ${result.skipped.length} skipped.`, result.skipped.length ? "warning" : "success");
      renderOccurrences(result.created, result.skipped, "Series created", true);
    } else {
      feedback($("#form-feedback"), `${roomMap.get(payload.room_id).name} is booked. See you on ${dateLabel(payload.start)}.`);
      $("#preview-result").hidden = true;
    }
    $("#calendar-date").value = payload.start.slice(0, 10);
    await refreshAfterMutation();
  } catch (error) { showError($("#form-feedback"), error); }
  finally { setFormBusy(false); }
});

$("#keep-booking").addEventListener("click", () => $("#cancel-dialog").close());
$("#confirm-cancel").addEventListener("click", async () => {
  if (!pendingCancellation) return;
  const {kind, id} = pendingCancellation;
  $("#confirm-cancel").disabled = true;
  try {
    const result = await api(`/${kind}/${id}/cancel`, {});
    $("#cancel-dialog").close();
    feedback($("#page-feedback"), kind === "series" ?
      `${result.cancelled_count} future dates cancelled. Past and ongoing meetings are unchanged.` : "Booking cancelled. The room is available for that time.");
    await refreshAfterMutation();
  } catch (error) {
    $("#cancel-dialog").close(); showError($("#page-feedback"), error);
  } finally { $("#confirm-cancel").disabled = false; pendingCancellation = null; }
});

const todayParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: rooms[0].timezone, year: "numeric", month: "2-digit", day: "2-digit",
}).formatToParts(new Date());
const today = ["year", "month", "day"].map((part) => todayParts.find((item) => item.type === part).value).join("-");
$("#calendar-date").value = today;
$("#start").value = `${today}T09:00`;
$("#end").value = `${today}T10:00`;
$("#repeat-until").value = today;
updateKind();
refresh().catch((error) => showError($("#page-feedback"), error));
