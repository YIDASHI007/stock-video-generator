import React from "react";
import {BrowserRouter, Navigate, Route, Routes} from "react-router-dom";

import {Layout} from "./components";
import {CreatePage} from "./pages/CreatePage";
import {AccountsPage} from "./pages/AccountsPage";
import {AnalyticsPage} from "./pages/AnalyticsPage";
import {BackupsPage} from "./pages/BackupsPage";
import {ContentLibraryPage} from "./pages/ContentLibraryPage";
import {DashboardPage} from "./pages/DashboardPage";
import {JobsPage} from "./pages/JobsPage";
import {MaterialsPage} from "./pages/MaterialsPage";
import {PreviewPage} from "./pages/PreviewPage";
import {PublishCalendarPage} from "./pages/PublishCalendarPage";
import {PublishPage} from "./pages/PublishPage";
import {PublishRecordsPage} from "./pages/PublishRecordsPage";
import {SettingsPage} from "./pages/SettingsPage";
import {SimulationPage} from "./pages/SimulationPage";
import {SystemLogsPage} from "./pages/SystemLogsPage";
import {WorkbenchPage} from "./pages/WorkbenchPage";
import {WorkflowsPage} from "./pages/WorkflowsPage";

export const App: React.FC = () => (
  <BrowserRouter>
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        <Route path="workbench" element={<WorkbenchPage />} />
        <Route path="create" element={<CreatePage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="assets" element={<ContentLibraryPage />} />
        <Route path="assets/materials" element={<MaterialsPage />} />
        <Route path="publish" element={<PublishPage />} />
        <Route path="publish/calendar" element={<PublishCalendarPage />} />
        <Route path="publish/records" element={<PublishRecordsPage />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="system/logs" element={<SystemLogsPage />} />
        <Route path="system/backups" element={<BackupsPage />} />
        <Route path="simulations/:simulationId" element={<SimulationPage />} />
        <Route
          path="simulations/:simulationId/preview"
          element={<PreviewPage />}
        />
        {/* 已废弃路由：重定向避免死链 */}
        <Route path="outputs" element={<Navigate to="/assets" replace />} />
        <Route path="health" element={<Navigate to="/settings" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  </BrowserRouter>
);
