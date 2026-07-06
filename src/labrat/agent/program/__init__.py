"""Program mode: restricted tool-pipeline DSL + interpreter (M4 2.2).

A program is an ordered JSON pipeline of registered-tool steps with handle
binding — NOT arbitrary code. Safe by construction: execution only ever
dispatches registered tools through the existing ToolRegistry.
"""
