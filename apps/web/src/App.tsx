import React from "react";
import {BrowserRouter, Navigate, Route, Routes} from "react-router-dom";

import {Layout} from "./components";
import {CreatePage} from "./pages/CreatePage";
import {AccountsPage} from "./pages/AccountsPage";
import {DashboardPage} from "./pages/DashboardPage";
import {JobsPage} from "./pages/JobsPage";
import {PreviewPage} from "./pages/PreviewPage";
import {PublishPage} from "./pages/PublishPage";
import {SettingsPage} from "./pages/SettingsPage";
import {SimulationPage} from "./pages/SimulationPage";
import {WorkbenchPage} from "./pages/WorkbenchPage";

export const App: React.FC = () => (
  <BrowserRouter>
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="workbench" element={<WorkbenchPage />} />
        <Route path="create" element={<CreatePage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="publish" element={<PublishPage />} />
        <Route path="accounts" element={<AccountsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="simulations/:simulationId" element={<SimulationPage />} />
        <Route
          path="simulations/:simulationId/preview"
          element={<PreviewPage />}
        />
        {/* 已废弃路由：重定向避免死链 */}
        <Route path="outputs" element={<Navigate to="/" replace />} />
        <Route path="health" element={<Navigate to="/settings" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  </BrowserRouter>
);
