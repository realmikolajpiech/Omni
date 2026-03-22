# Omni Context Engine  
## Technical Specification  
Internal Document — March 2026  
Omni — heyomni.app  

---

## 1. Overview

This document describes the technical architecture and implementation plan for Omni’s Context Engine — the core system that transforms Omni from a launcher with AI features into an intelligent desktop assistant that understands what you’re doing and gives you what you need before you ask.

**Core principle:** Omni observes the user’s active work context (current app, open files, recent emails, upcoming events) and maintains a local knowledge graph that links files, people, events, and communications. This graph powers both reactive search (user asks) and proactive suggestions (Omni initiates).

---

## 2. System Architecture

The Context Engine consists of five layers, each building on the previous one.

### 2.1 Activity Observer

**Purpose:** Continuously track what the user is doing on their computer — which app is active, which file is open, which URL is in the browser.

#### macOS Implementation

- `NSWorkspace.shared.frontmostApplication` — observes active application  
- Accessibility API (AX) — reads window title, document name, file path  
- FSEvents / DispatchSource — monitors file system changes  
- NSMetadataQuery — Spotlight metadata  
- Browser integration — extension or AppleScript for URLs  

#### Windows Implementation

- `SetWinEventHook` / UI Automation — foreground window tracking  
- `ReadDirectoryChangesW` — file system watcher  
- Windows Search Index — file metadata  

#### Data Collected Per Event

- Timestamp (ms precision)  
- App bundle ID / executable  
- Window title / document name  
- File path (if applicable)  
- Duration of focus  

#### Privacy

All data stays local. No screenshots, no keylogging, no content capture. Only metadata is stored. Users can pause or delete data anytime.

---

### 2.2 Local Knowledge Graph

**Purpose:** Store relationships between entities in a local graph database.

#### Entity Types

- **File** — path, name, type, embeddings, summary  
- **Person** — name, email, relationships  
- **Event** — calendar entry  
- **Email** — metadata + attachments  
- **URL** — visited pages  
- **WorkSession** — grouped activity  

#### Relationship Types

- file → opened_during → event  
- file → received_from → person  
- file → related_to → file  
- person → attendee_of → event  
- email → references → file  
- file → part_of → work_session  

#### Storage

- SQLite + JSON columns  
- Separate vector index for embeddings  
- Query via SQL joins  
- Expected size: <100MB after 6 months  

---

### 2.3 Semantic Index (Enhanced)

**Enhancements:**

- Incremental indexing  
- Multi-modal indexing (OCR, code parsing)  
- Metadata enrichment  
- Priority queue indexing  

---

### 2.4 Context Matcher

**Purpose:** Match current activity with relevant entities.

#### Flow

1. Activity signal received  
2. Entity extraction  
3. Graph query  
4. Relevance ranking  
5. Output top 3–5 results  

#### Ranking Factors

- Recency (last 7 days weighted 3×)  
- Relationship strength  
- Access frequency  

#### Performance Target

- <200ms total  

---

### 2.5 Proactive Suggestion Engine

**Purpose:** Deliver relevant suggestions automatically.

#### Trigger Types

- Calendar trigger  
- File trigger  
- Pattern trigger  
- Stale file trigger  

#### Notification Rules

- Max 5 per day  
- No interruption in full-screen  
- Group related notifications  
- Learn from dismissals  
- Always actionable  

---

## 3. Feature Specifications

### 3.1 Enhanced Semantic File Search

**Target Improvements:**

- Context-aware ranking  
- Recency weighting  
- Entity-based boosting  

**Scoring Formula:**
final_score = 0.5 * semantic_similarity + 0.3 * context_relevance + 0.2 * recency_score


---

### 3.2 Meeting Context Preparation

**Solution:**

- Pre-meeting context card  
- Shows:
  - People  
  - Documents  
  - Meeting history  
  - Action items  

---

### 3.3 Intelligent File Organization

**Features:**

- Auto-classification  
- Source tracking  
- Smart folder suggestions  
- Batch organization  

**Constraint:**  
Omni never moves files automatically without user confirmation.

---

### 3.4 Work Session Continuity

**Features:**

- Session summaries  
- One-click resume  
- Session clustering  

---

### 3.5 Cross-App Entity Linking

**Examples:**

- Person card → emails, files, meetings  
- Project card → documents, threads, notes  

---

## 4. Implementation Priority

### Phase 1: Foundation (2–3 weeks)

- Activity Observer  
- Knowledge Graph schema  
- Basic relationships  

### Phase 2: Context-Aware Search (1–2 weeks)

- Context re-ranking  
- Entity cards  

### Phase 3: Proactive Suggestions (2–3 weeks)

- Meeting prep  
- File classification  
- Notification UI  

### Phase 4: Session Continuity (1–2 weeks)

- Session clustering  
- Resume UI  

### Phase 5: Windows Port

- Windows Activity Observer  

---

## 5. Privacy Architecture

- All data local  
- Transparent data view  
- Granular controls  
- Configurable retention (default 90 days)  
- No screenshots or keylogging  

---

## 6. Technical Constraints

- CPU: <1%  
- RAM: <50MB  
- Disk: <200MB/year  
- Battery-aware processing  
- API cost target: <$0.01/day/user  

#### Permissions

- Accessibility  
- Calendar  
- Mail  
- Full Disk Access  

System must degrade gracefully if permissions are missing.