import type {VisualizationSpec} from "@stock-video/schemas";
import {
  ScrollingFilledAreaStockVideo,
  StoryNarrativePrototypeVideo,
  StoryNarrativeStockVideo,
  StockHookStyleLandscapeCover,
  StockHookStylePortraitCover,
  StockLandscapeCover,
  StockPortraitCover,
  StockResultShockLandscapeCover,
  StockResultShockPortraitCover,
  StockVideo,
} from "@stock-video/video-template";
import React from "react";
import {Composition} from "remotion";

import {sampleSpec} from "./sample-spec";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="StockFilledAreaPrototype"
        component={ScrollingFilledAreaStockVideo}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={
          sampleSpec.composition.duration_seconds * sampleSpec.composition.fps
        }
        fps={sampleSpec.composition.fps}
        width={sampleSpec.composition.width}
        height={sampleSpec.composition.height}
        calculateMetadata={({props}) => {
          const spec = props.spec as VisualizationSpec;
          return {
            durationInFrames: Math.round(
              spec.composition.duration_seconds * spec.composition.fps,
            ),
            fps: spec.composition.fps,
            width: spec.composition.width,
            height: spec.composition.height,
            props,
          };
        }}
      />
      <Composition
        id="StoryNarrativePrototype"
        component={StoryNarrativePrototypeVideo}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={
          sampleSpec.composition.duration_seconds * sampleSpec.composition.fps
        }
        fps={sampleSpec.composition.fps}
        width={sampleSpec.composition.width}
        height={sampleSpec.composition.height}
        calculateMetadata={({props}) => {
          const spec = props.spec as VisualizationSpec;
          return {
            durationInFrames: Math.round(
              spec.composition.duration_seconds * spec.composition.fps,
            ),
            fps: spec.composition.fps,
            width: spec.composition.width,
            height: spec.composition.height,
            props,
          };
        }}
      />
      <Composition
        id="StockHistoricalSimulationV1"
        component={StoryNarrativeStockVideo}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={
          sampleSpec.composition.duration_seconds * sampleSpec.composition.fps
        }
        fps={sampleSpec.composition.fps}
        width={sampleSpec.composition.width}
        height={sampleSpec.composition.height}
        calculateMetadata={({props}) => {
          const spec = props.spec as VisualizationSpec;
          return {
            durationInFrames: Math.round(
              spec.composition.duration_seconds * spec.composition.fps,
            ),
            fps: spec.composition.fps,
            width: spec.composition.width,
            height: spec.composition.height,
            props,
          };
        }}
      />
      {/* 兼容旧调用方：历史 ID 与滚动入口均固定指向当前 V1。 */}
      <Composition
        id="StockHistoricalSimulation"
        component={StoryNarrativeStockVideo}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={
          sampleSpec.composition.duration_seconds * sampleSpec.composition.fps
        }
        fps={sampleSpec.composition.fps}
        width={sampleSpec.composition.width}
        height={sampleSpec.composition.height}
        calculateMetadata={({props}) => {
          const spec = props.spec as VisualizationSpec;
          return {
            durationInFrames: Math.round(
              spec.composition.duration_seconds * spec.composition.fps,
            ),
            fps: spec.composition.fps,
            width: spec.composition.width,
            height: spec.composition.height,
            props,
          };
        }}
      />
      <Composition
        id="StockScrollingSimulation"
        component={StoryNarrativeStockVideo}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={
          sampleSpec.composition.duration_seconds * sampleSpec.composition.fps
        }
        fps={sampleSpec.composition.fps}
        width={sampleSpec.composition.width}
        height={sampleSpec.composition.height}
        calculateMetadata={({props}) => {
          const spec = props.spec as VisualizationSpec;
          return {
            durationInFrames: Math.round(
              spec.composition.duration_seconds * spec.composition.fps,
            ),
            fps: spec.composition.fps,
            width: spec.composition.width,
            height: spec.composition.height,
            props,
          };
        }}
      />
      <Composition
        id="StockHistoricalSimulationLegacy"
        component={StockVideo}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={
          sampleSpec.composition.duration_seconds * sampleSpec.composition.fps
        }
        fps={sampleSpec.composition.fps}
        width={sampleSpec.composition.width}
        height={sampleSpec.composition.height}
        calculateMetadata={({props}) => {
          const spec = props.spec as VisualizationSpec;
          return {
            durationInFrames: Math.round(
              spec.composition.duration_seconds * spec.composition.fps,
            ),
            fps: spec.composition.fps,
            width: spec.composition.width,
            height: spec.composition.height,
            props,
          };
        }}
      />
      <Composition
        id="StockCoverPortrait"
        component={StockResultShockPortraitCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1080}
        height={1440}
      />
      <Composition
        id="StockCoverLandscape"
        component={StockResultShockLandscapeCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1440}
        height={1080}
      />
      <Composition
        id="StockCoverPortraitLegacy"
        component={StockPortraitCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1080}
        height={1440}
      />
      <Composition
        id="StockCoverLandscapeLegacy"
        component={StockLandscapeCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1440}
        height={1080}
      />
      <Composition
        id="StockHookCoverPortraitPrototype"
        component={StockHookStylePortraitCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1080}
        height={1440}
      />
      <Composition
        id="StockHookCoverLandscapePrototype"
        component={StockHookStyleLandscapeCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1440}
        height={1080}
      />
      <Composition
        id="StockResultShockCoverPortraitPrototype"
        component={StockResultShockPortraitCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1080}
        height={1440}
      />
      <Composition
        id="StockResultShockCoverLandscapePrototype"
        component={StockResultShockLandscapeCover}
        defaultProps={{spec: sampleSpec}}
        durationInFrames={1}
        fps={30}
        width={1440}
        height={1080}
      />
    </>
  );
};
