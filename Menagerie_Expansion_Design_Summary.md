# Menagerie Expansion Design Summary

## Purpose

This document summarizes the planned expansion of Menagerie from a
traditional self-hosted collection manager into an integrated platform
for collection management, heraldry design, knowledge management, image
annotation, and AI-assisted classification.

The goal is to make Menagerie useful not only for organizing
collections, but also for documenting the knowledge surrounding them
while providing tooling to build custom AI models for each installation.

------------------------------------------------------------------------

# Design Philosophy

## Knowledge First

Every important concept should exist as a reusable object rather than
duplicated metadata.

-   A symbol exists once.
-   Heraldry references it.
-   Wiki pages describe it.
-   Image annotations reference it.
-   AI models classify it.
-   Family trees display it.

Everything references the same knowledge object.

## Human in Control

AI assists users---it never replaces them.

-   AI suggestions are always reviewable.
-   Training samples require administrator approval.
-   Administrators never need ML expertise.

## Structured Rather Than Flat

Menagerie stores structured knowledge:

-   Heraldry
-   SVG symbols
-   Family relationships
-   Wiki relationships
-   Image annotations
-   Dataset versions
-   Model versions

------------------------------------------------------------------------

# Major Subsystems

## Heraldry Studio

-   Structured heraldry builder
-   Multiple shield templates
-   Heraldic field divisions
-   Reusable SVG symbols
-   Administrator-uploaded SVG decals
-   Fillable vector regions
-   Rotation, scaling, mirroring
-   SVG/PNG rendering
-   Heraldry linked to people and wiki pages

## Lineage Tree

-   Heraldry displayed beside each person's name
-   Parent/child/marriage relationships
-   Houses and titles
-   Heraldry evolution
-   Linked biographies and collection items

## Symbol Atlas

-   Reusable SVG symbol library
-   Categories and variants
-   Meanings and descriptions
-   AI class mapping
-   Wiki integration
-   Version history

## Knowledge Graph & Wiki

Important entities become first-class wiki pages.

Supported pages include:

-   People
-   Houses
-   Symbols
-   Heraldry
-   Organizations
-   Events
-   Locations
-   Collection Items

Typed relationships connect all objects.

## Image Annotation

-   Rectangle
-   Polygon
-   Ellipse
-   Point
-   Freehand mask

Annotations reference Symbol Atlas entries.

Workflow:

1.  User labels image
2.  Submit for review
3.  Administrator approves
4.  Training pool updated

Original labeler, reviewer, and revision history are preserved.

## AI Lab

Administrator workflow:

1.  Collect samples
2.  Approve labels
3.  Train
4.  Review
5.  Deploy

Features:

-   Hidden dataset splitting
-   Versioned datasets
-   External export
-   External model import
-   Evaluation
-   Deployment and rollback
-   AI-assisted labeling

## AI Metrics

### User

-   Active users
-   Item additions
-   Image uploads
-   Heraldry created
-   Wiki edits

### AI

-   Supported classes
-   Labels by state
-   Queue backlog
-   Classes needing samples
-   Training runs
-   Model versions
-   Suggestion acceptance rate

## Permissions

Capability-based permissions include:

-   Image labeling
-   Annotation review
-   SVG upload
-   Symbol management
-   AI administration
-   Model deployment

------------------------------------------------------------------------

# Long-Term Vision

Menagerie evolves into a unified platform that combines:

-   Collection Manager
-   Heraldry Designer
-   Lineage Explorer
-   Knowledge Graph
-   Visual Annotation Tool
-   AI Dataset Builder
-   AI Operations Platform

Every person, symbol, heraldry design, annotation, wiki page, and AI
class is connected through shared knowledge objects.
