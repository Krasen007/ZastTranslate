# Tasteful Software Guide for AI Agents

## Core Principle

Taste is the ability to make good decisions about what to include and what to exclude. It is a compression function: given the infinite space of things you could build, taste selects the finite set of things you should build.

---

## Strict Dos

### Feature Selection

- **DO** start by identifying the single core problem you're solving
- **DO** build the minimum set of features that solve that problem exceptionally well
- **DO** make every feature earn its place through necessity, not possibility
- **DO** consider the negative externalities of each feature on cognitive load and complexity
- **DO** remove features when their complexity cost exceeds their utility gain
- **DO** focus on getting defaults exactly right
- **DO** optimize the empty state to be welcoming and clear
- **DO** ensure the first ten seconds of the experience feel effortless

### Decision-Making

- **DO** apply taste at every level: features, interactions, API responses, error states, data flow, defaults
- **DO** treat every default as a deliberate taste decision
- **DO** treat every omission as a deliberate taste decision
- **DO** build for unarticulated user needs, not just stated preferences
- **DO** develop a theory of mind about your users and their context
- **DO** make choices that serve a coherent point of view about the problem domain
- **DO** accumulate value over time through consistent, opinionated decisions
- **DO** find the right amount - the mean between extremes (Aristotle's principle of calibration)
- **DO** treat taste as dynamic - a point of view compounds through deliberate refinement

### Execution

- **DO** follow the "weniger, aber besser" principle: less, but better
- **DO** design by subtraction - ask "what can I remove" at every stage
- **DO** ensure every interaction feels considered
- **DO** make the design invisible when well-executed - the product should just work quietly
- **DO** prioritize coherence and logical consistency across the entire product
- **DO** understand your users' workflow deeply enough that no agent can replicate it from scratch
- **DO** embed understanding through years of specific decisions, not replicable from a prompt

### Product Shape

- **DO** recognize that the entire shape of a product is the accumulated residue of thousands of small taste decisions
- **DO** make choices that users may never consciously notice but will absolutely feel
- **DO** build products that have a coherent theory about the problem domain
- **DO** create a point of view that compounds value over time

---

## Strict Don'ts

### Feature Selection

- **DON'T** build features just because you can - code being cheap is not a reason to add
- **DON'T** add features without considering their interaction with existing features
- **DON'T** increase surface area without decreasing cognitive load elsewhere
- **DON'T** sacrifice focus for breadth
- **DON'T** let the absence of engineering constraints become a reason to build everything
- **DON'T** add features that don't serve the core problem
- **DON'T** build for feature parity with competitors
- **DON'T** treat features as purely additive - recognize their negative externalities

### Decision-Making

- **DON'T** rely on stated preferences alone - users can't describe what doesn't exist yet
- **DON'T** optimize for local maxima through A/B testing without a coherent vision
- **DON'T** make decisions based solely on available data points
- **DON'T** be backward-looking - don't just compress the past, imagine a better future
- **DON'T** defer taste decisions to "later" - every default and omission is a taste decision
- **DON'T** separate aesthetics from function - taste is not a coat of paint
- **DON'T** build without a point of view about how the product should work
- **DON'T** rely on inertia as a moat - switching costs are approaching zero

### Execution

- **DON'T** announce cleverness through the design
- **DON'T** let the design get in the way of the experience
- **DON'T** sacrifice simplicity for perceived completeness
- **DON'T** add complexity that doesn't serve a clear purpose
- **DON'T** build without understanding the full context of user workflow
- **DON'T** replicate patterns without considering if they belong

### Product Shape

- **DON'T** let the product shape be determined by engineering constraints
- **DON'T** accumulate features without accumulating coherent vision
- **DON'T** build disconnected features that don't serve a unified theory
- **DON'T** sacrifice the overall argument the product makes about how the world should work

---

## The Taste Test

Before building or adding anything, ask:

1. **Necessity**: Is this essential to solving the core problem, or just nice to have?
2. **Externalities**: How does this increase cognitive load or system complexity?
3. **Coherence**: Does this align with our point of view about the problem domain?
4. **Calibration**: Is this the right amount, or too much/too little?
5. **Omission**: What are we choosing NOT to build by building this?
6. **Unarticulated Need**: Does this serve something users can't articulate but will recognize when they experience it?

If you cannot answer these questions clearly and affirmatively, do not build it.

---

## When Taste Says Yes

Taste is not a synonym for restraint. Sometimes the right decision is a bold, contrarian bet that rejects the available data because the team's model of the user is stronger than what the data can yet show.

- **DO** make the contrarian bet when your model of the user is stronger than the available data
- **DO** distinguish between removing something because it is noise and removing it because you are afraid to commit
- **DO** use subtraction to serve a point of view; minimalism without a point of view is just emptiness

The goal is not to remove as much as possible. The goal is to make the fewest choices necessary to express a clear belief about what users need.

---

## Failure Modes and Escape Hatches

When the taste test is ambiguous, use these defaults:

- **Default to omission**: When uncertain, don't build it - features are easy to add and hard to remove
- **Reversibility test**: Prefer decisions that are cheap to reverse when your taste might be wrong

---

## The Anti-Patterns to Avoid

### The Feature Factory

- Building every feature you can think of because code is cheap
- Measuring success by feature count rather than user experience
- Treating features as additive without considering their interactions

### The CRUD App

- A structured workflow wrapped in a UI with no deeper point of view
- Value proposition: "we built this workflow so you don't have to"
- Defensibility based purely on "we already built this and switching is annoying"

### The Aesthetic-Only Product

- Clean UI and good onboarding without deeper taste
- Superficial polish without considered interactions
- Decoration rather than judgment

### The Data-Driven Without Vision

- Optimizing for stated preferences without a theory of what users actually need
- A/B testing to local maxima without coherent direction
- Backward-looking design that compresses the past rather than imagining a better future

### The Everything Machine

- Building every plausible feature because the team can, without deciding what the product is for
- Treating an ever-expanding roadmap as evidence of ambition or completeness
- Team A behavior: optimizing for visible activity and feature count instead of a coherent user experience

---

## The Ideal State

A tasteful product:

- Has a coherent theory about its problem domain
- Makes every interaction feel considered
- Gets defaults exactly right
- Optimizes the empty state
- Makes the first ten seconds effortless
- Has a point of view that compounds over time
- Doesn't call attention to itself - it just works quietly
- Is designed by subtraction, not addition
- Serves unarticulated user needs
- Has taste baked into every layer, not just the surface

Remember: When the cost of building approaches zero, the ability to decide what NOT to build becomes the entire product. That is taste.
