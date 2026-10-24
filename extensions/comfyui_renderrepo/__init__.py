# SPDX-License-Identifier: GPL-3.0-only
"""Install the RR Python package in Comfy's environment before loading this node."""
import folder_paths
from server import PromptServer
from renderrepo.comfy_extension import register

NODE_CLASS_MAPPINGS = {}
WEB_DIRECTORY = './web'
register(PromptServer.instance.routes, folder_paths)
